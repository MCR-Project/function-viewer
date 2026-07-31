import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// requestAnimationFrame never fires while the tab is hidden, which stalls
// React Flow's node measurement and d3 transitions. Fall back to timers so
// the graph keeps working in backgrounded/hidden windows.
const nativeRaf = window.requestAnimationFrame.bind(window)
const nativeCancel = window.cancelAnimationFrame.bind(window)
window.requestAnimationFrame = (cb: FrameRequestCallback): number => {
  if (document.visibilityState === 'hidden') {
    return window.setTimeout(() => cb(performance.now()), 16)
  }
  return nativeRaf(cb)
}
window.cancelAnimationFrame = (id: number): void => {
  window.clearTimeout(id)
  nativeCancel(id)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
