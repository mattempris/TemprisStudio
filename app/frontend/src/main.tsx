import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyStoredPalette } from './lib/palette'

// Before first paint, so a non-default palette does not flash the light theme.
applyStoredPalette()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
