@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------------------
REM Stops the JAStudio dev servers.
REM
REM Kills strictly by LISTENING PORT, never by image name. Killing "python.exe"
REM would take down unrelated work -- training runs, notebooks -- that has
REM nothing to do with this app.
REM ---------------------------------------------------------------------------

REM Vite falls back to 5174, 5175... when 5173 is taken, so stopping only 5173
REM leaves orphans behind that survive every restart. Sweep the fallback range.
set "PORTS=9400 5173 5174 5175 5176"

echo ===============================================
echo   Stopping Tempris JAStudio
echo ===============================================
echo.

set "FOUND="
for %%p in (%PORTS%) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p" ^| findstr /i "LISTENING"') do (
    if not "%%a"=="0" (
      REM /c: forces a literal match. Without it findstr splits on the space and
      REM searches for "Image" OR "Name:", which also hits "Session Name:".
      for /f "tokens=2,*" %%n in ('tasklist /fi "PID eq %%a" /fo list ^| findstr /c:"Image Name:"') do (
        echo   port %%p  PID %%a  %%o
      )
      taskkill /pid %%a /t /f >nul 2>&1
      if not errorlevel 1 (
        echo     stopped.
        set "FOUND=1"
      ) else (
        echo     could not stop PID %%a -- you may need to close its window manually.
      )
    )
  )
)

echo.
if defined FOUND (
  echo Done.
) else (
  echo Nothing was listening on %PORTS% -- already stopped.
)
echo.
ping -n 6 127.0.0.1 >nul
exit /b 0
