import { useEffect, useRef, useState } from 'react';

type CanvasTool = 'pan' | 'rescan' | 'pen' | 'multiselect' | 'wand';

/**
 * OSD `Viewer.setMouseNavEnabled` -> `innerTracker.setTracking` melempar
 * "Cannot read properties of undefined (reading 'tracking')" saat tracker sudah
 * di-destroy (`MouseTracker.destroy` men-null-kan delegate-nya). Satu frame React
 * masih bisa memegang viewer yang sudah mati, jadi guard + try/catch wajib.
 */
export function applyMouseNav(viewer: any, enabled: boolean): void {
  if (!viewer || !viewer.innerTracker || !viewer.viewport) return;
  try {
    viewer.setMouseNavEnabled(enabled);
  } catch {
    /* viewer destroyed mid-flight */
  }
}

/** Keep the SVG layer attached when OpenSeadragon reopens a sheet. */
export function useCanvasOverlay(
  viewer: any,
  osdModule: any,
  width: number,
  height: number,
  traceTool: CanvasTool,
) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [tempPan, setTempPan] = useState(false);
  const traceToolRef = useRef(traceTool);
  const tempPanRef = useRef(tempPan);
  traceToolRef.current = traceTool;
  tempPanRef.current = tempPan;

  useEffect(() => {
    if (!viewer || !osdModule || !width || !height) return;

    const overlay = document.createElement('div');
    overlay.id = 'pid-interactive-svg-overlay-container';
    overlay.style.width = '100%';
    overlay.style.height = '100%';
    overlay.style.position = 'absolute';
    overlay.style.top = '0';
    overlay.style.left = '0';

    const rect = new osdModule.Rect(0, 0, 1.0, height / width);
    const attachOverlay = () => {
      // viewer.open() clears overlays before opening the new image.
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      try {
        viewer.addOverlay({ element: overlay, location: rect, checkResize: false });
      } catch {
        return; // viewer is being destroyed
      }
      // Mouse nav can only be applied once the viewer finished opening; doing it here
      // (instead of only in the effect below) is what keeps pan/pointer-events correct
      // after a reopen, and the guard keeps a destroyed tracker from throwing.
      applyMouseNav(viewer, tempPanRef.current || traceToolRef.current === 'pan');
      overlay.style.pointerEvents = tempPanRef.current || traceToolRef.current === 'pan'
        ? 'none' : 'auto';
    };

    attachOverlay();
    setContainer(overlay);
    viewer.addHandler('open', attachOverlay);

    return () => {
      try { viewer.removeHandler('open', attachOverlay); } catch {}
      try { viewer.removeOverlay(overlay); } catch {}
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      setContainer(null);
    };
  }, [viewer, osdModule, width, height]);

  // Ctrl/Cmd or Space temporarily turns an editing tool into a hand-pan tool.
  useEffect(() => {
    if (!viewer) return;
    const isTyping = (target: EventTarget | null) => {
      const element = target as HTMLElement | null;
      return !!element && (
        element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' ||
        element.tagName === 'SELECT' || element.isContentEditable
      );
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      if (event.key === 'Control' || event.key === 'Meta' || event.key === ' ') {
        if (event.key === ' ') event.preventDefault();
        setTempPan(true);
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key === 'Control' || event.key === 'Meta' || event.key === ' ') setTempPan(false);
    };
    const onBlur = () => setTempPan(false);
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, [viewer]);

  useEffect(() => {
    if (!viewer || !container) return;
    const enablePan = tempPan || traceTool === 'pan';
    applyMouseNav(viewer, enablePan);
    container.style.pointerEvents = enablePan ? 'none' : 'auto';
    container.style.cursor = tempPan ? 'grab' : '';
  }, [viewer, container, tempPan, traceTool]);

  return { container, tempPan };
}
