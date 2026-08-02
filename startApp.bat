@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------------------
REM Tempris JAStudio launcher: starts backend + frontend, opens a browser tab.
REM
REM Each server gets its own window so you can watch pipeline progress and stop
REM it with Ctrl+C. Closing THIS window does not stop them.
REM ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "BACKEND=%ROOT%app\backend"
set "FRONTEND=%ROOT%app\frontend"
set "BACKEND_PORT=9400"
set "FRONTEND_URL=http://localhost:5173"

REM Override by setting JASTUDIO_PYTHON before running, if the env lives elsewhere.
if not defined JASTUDIO_PYTHON (
  set "JASTUDIO_PYTHON=%USERPROFILE%\.conda\envs\jastudio-backend\python.exe"
)

echo ===============================================
echo   Tempris JAStudio
echo ===============================================
echo.

REM --- Preflight ------------------------------------------------------------

if not exist "%JASTUDIO_PYTHON%" (
  echo [X] Python env not found:
  echo     %JASTUDIO_PYTHON%
  echo.
  echo     Expected the 'jastudio-backend' conda env. If it is elsewhere, set
  echo     JASTUDIO_PYTHON to its python.exe and re-run.
  goto :fail
)

if not exist "%BACKEND%\.env" (
  echo [X] Missing %BACKEND%\.env
  echo     It holds the Anthropic key and Azure service principal. See app\README.md.
  goto :fail
)

REM A stale server already holding a port is the trap worth guarding. For the
REM backend a second uvicorn cannot bind, exits quietly, and the OLD code keeps
REM answering -- which looks like your changes silently doing nothing. For the
REM frontend, Vite instead moves to 5174, so the browser tab we open would point
REM at the stale one.
netstat -ano | findstr ":%BACKEND_PORT%" | findstr /i "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [!] Port %BACKEND_PORT% is in use -- a backend is already running.
  echo     If it is an older build it will keep serving stale code.
  choice /c RQ /n /m "     [R]euse it, or [Q]uit and run stopApp.bat first? "
  if errorlevel 2 goto :fail
  set "SKIP_BACKEND=1"
  echo     Reusing the running backend.
)

netstat -ano | findstr ":5173" | findstr /i "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [!] Port 5173 is in use -- a dev server is already running.
  echo     Starting another would land on 5174 while this opens 5173.
  choice /c RQ /n /m "     [R]euse it, or [Q]uit and run stopApp.bat first? "
  if errorlevel 2 goto :fail
  set "SKIP_FRONTEND=1"
  echo     Reusing the running frontend.
)

REM An orphan on a fallback port is invisible otherwise: it serves stale code to
REM anyone who has that URL open, and until stopApp swept the range it survived
REM every restart.
for %%q in (5174 5175 5176) do (
  netstat -ano | findstr ":%%q" | findstr /i "LISTENING" >nul 2>&1
  if not errorlevel 1 (
    echo [!] Something is also listening on %%q -- probably an orphaned dev server
    echo     from an earlier run, serving stale code. stopApp.bat clears it.
  )
)

if not exist "%FRONTEND%\node_modules" (
  echo [*] Installing frontend dependencies ^(first run, this takes a minute^)...
  pushd "%FRONTEND%"
  call npm install
  if errorlevel 1 (
    popd
    echo [X] npm install failed.
    goto :fail
  )
  popd
  echo.
)

REM --- Start servers --------------------------------------------------------

REM `start /d <dir>` sets the new window's working directory. Doing it that way
REM rather than embedding a `cd` matters: quotes nested inside an already-quoted
REM `cmd /k "..."` argument terminate it early, and the command silently never
REM runs. The doubled quote before the exe path is the standard escape that lets
REM cmd /k accept a quoted executable.
if not defined SKIP_BACKEND (
  echo [*] Starting backend on port %BACKEND_PORT%...
  REM No --reload: this repo sits on a OneDrive-synced path where the file
  REM watcher misses changes. Restart after editing backend code.
  start "JAStudio backend" /d "%BACKEND%" cmd /k ""%JASTUDIO_PYTHON%" -u -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"
)

REM --- Wait for the backend BEFORE starting the frontend ---------------------
REM
REM Vite is ready in well under a second; the backend takes a few. Starting them
REM together leaves a window where any already-open browser tab reconnects
REM through Vite's HMR and fires its API calls at a port nothing is listening on,
REM filling the frontend log with ECONNREFUSED. Ordering it this way removes the
REM window rather than papering over it.

echo.
echo [*] Waiting for the backend to come up...
set "BACKEND_OK="
for /l %%i in (1,1,45) do (
  curl -s -m 2 http://localhost:%BACKEND_PORT%/health 2>nul | findstr /c:"\"status\":\"ok\"" >nul 2>&1
  if not errorlevel 1 (
    set "BACKEND_OK=1"
    goto :backend_up
  )
  ping -n 2 127.0.0.1 >nul
)
:backend_up
if defined BACKEND_OK (
  echo     backend ready.
) else (
  echo [!] Backend did not respond within 45s. Check its window for errors --
  echo     starting the frontend anyway so you can see the page.
)

if not defined SKIP_FRONTEND (
  echo [*] Starting frontend...
  start "JAStudio frontend" /d "%FRONTEND%" cmd /k "npm run dev"
)

echo [*] Waiting for the frontend...
set "FRONTEND_OK="
for /l %%i in (1,1,45) do (
  curl -s -m 2 -o nul "%FRONTEND_URL%" 2>nul
  if not errorlevel 1 (
    set "FRONTEND_OK=1"
    goto :frontend_up
  )
  ping -n 2 127.0.0.1 >nul
)
:frontend_up
if defined FRONTEND_OK (
  echo     frontend ready.
) else (
  echo [!] Frontend did not respond within 45s. Check its window.
)

REM --- Open browser ---------------------------------------------------------

echo.
if defined FRONTEND_OK (
  echo [*] Opening %FRONTEND_URL%
  start "" "%FRONTEND_URL%"
) else (
  echo     Not opening a browser tab -- the frontend is not answering yet.
  echo     Once its window settles, go to %FRONTEND_URL%
)

echo.
echo ===============================================
echo   Running. Servers are in their own windows;
echo   closing this one leaves them up.
echo   Stop them with Ctrl+C there, or stopApp.bat.
echo ===============================================
echo.
ping -n 9 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo Startup aborted.
pause
exit /b 1
