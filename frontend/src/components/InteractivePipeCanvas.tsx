'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  Scissors,
  Check,
  X,
  Palette,
  Trash2,
  Tag,
  Sparkles,
  Crop,
  Pencil,
  Hand,
  RotateCcw,
  Plus,
  GripVertical,
} from 'lucide-react';
import { PipeRun, PipingID } from '@/types/schema';

// 5 Quick Colors: Biru #2563EB, Hijau #10B981, Merah #EF4444, Kuning #F59E0B, Ungu #8B5CF6
export const QUICK_COLORS = [
  { name: 'Biru Netral (Default)', hex: '#2563EB' },
  { name: 'Hijau (Process)', hex: '#10B981' },
  { name: 'Merah (Corrosion)', hex: '#EF4444' },
  { name: 'Kuning (Hazard)', hex: '#F59E0B' },
  { name: 'Ungu (Spec Break)', hex: '#8B5CF6' },
];

interface InteractivePipeCanvasProps {
  viewer: any;
  osdModule: any;
  width: number;
  height: number;
  runs: PipeRun[];
  pipingIds: PipingID[];
  showOverlay: boolean;
  opacity: number;
  selectedRunIndices: Set<number>;
  onSelectRunIndices: (indices: Set<number>) => void;
  onRecolorRuns: (runIdxs: number[], newColor: string) => void;
  onSplitRun: (runIdx: number, x: number, y: number) => Promise<void>;
  onDeleteRuns?: (runIdxs: number[]) => Promise<void>;
  onUpdateRunLabel?: (runIdx: number, label: string) => Promise<void>;
  onUpdateRunPoints?: (runIdx: number, points: [number, number][]) => Promise<void>;
  splitMode: boolean;
  onSetSplitMode: (active: boolean) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  traceTool: 'pan' | 'rescan' | 'pen';
  onSetTraceTool: (tool: 'pan' | 'rescan' | 'pen') => void;
  onRescan: (bounds: { x1: number; y1: number; x2: number; y2: number }, replaceExisting?: boolean) => Promise<void>;
  onManualRun: (points: [number, number][]) => Promise<void>;
  // Optional mode-driven color override: maps run index -> CSS color.
  // Used for Corrosion System / Circuit views so circuit coloring is rendered
  // as a vector layer on the raw CAD image instead of swapping to a server PNG.
  colorOverrideMap?: Map<number, string> | null;
  dimUncolored?: boolean;
}

export default function InteractivePipeCanvas({
  viewer,
  osdModule,
  width,
  height,
  runs,
  pipingIds,
  showOverlay,
  opacity,
  selectedRunIndices,
  onSelectRunIndices,
  onRecolorRuns,
  onSplitRun,
  onDeleteRuns,
  onUpdateRunLabel,
  onUpdateRunPoints,
  splitMode,
  onSetSplitMode,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  traceTool,
  onSetTraceTool,
  onRescan,
  onManualRun,
  colorOverrideMap,
  dimUncolored,
}: InteractivePipeCanvasProps) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [hoveredRunIdx, setHoveredRunIdx] = useState<number | null>(null);
  const [splitPreview, setSplitPreview] = useState<{ x: number; y: number } | null>(null);
  const [customColor, setCustomColor] = useState('#2563EB');
  const [splitting, setSplitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [savingTag, setSavingTag] = useState(false);
  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number; imgX: number; imgY: number } | null>(null);
  const [tagInput, setTagInput] = useState<string>('');
  // Draggable popover: track pointer grab offset relative to popover top-left
  const [popoverDragging, setPopoverDragging] = useState(false);
  const popoverDragRef = useRef<{ offsetX: number; offsetY: number; moved: boolean } | null>(null);

  // Box Trace state
  const [roiStart, setRoiStart] = useState<{ x: number; y: number } | null>(null);
  const [roiRect, setRoiRect] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [roiPendingModal, setRoiPendingModal] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);

  // Manual Pen state
  const [manualPoints, setManualPoints] = useState<[number, number][]>([]);
  const [penHoverPt, setPenHoverPt] = useState<[number, number] | null>(null);
  const [magnetSnapped, setMagnetSnapped] = useState<boolean>(false);

  // Vertex Dragging state
  const [draggingVertex, setDraggingVertex] = useState<{
    runIdx: number;
    ptIdx: number;
  } | null>(null);
  const [liveDragPoints, setLiveDragPoints] = useState<[number, number][] | null>(null);
  const [snapGuide, setSnapGuide] = useState<{ axis: 'h' | 'v'; val: number } | null>(null);

  const [toolBusy, setToolBusy] = useState(false);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const pointerDownRecordRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const traceToolRef = useRef(traceTool);
  traceToolRef.current = traceTool;

  // Mount OpenSeadragon overlay container that syncs with pan & zoom.
  //
  // IMPORTANT: `viewer.open()` internally calls `close()`, which runs
  // `clearOverlays()` and wipes the overlays container. The parent swaps the
  // base image via `viewer.open()` on mode change (digitize <-> system/circuit),
  // so we must RE-ATTACH our overlay every time the viewer (re)opens, otherwise
  // the SVG tracing layer silently disappears and is never restored.
  useEffect(() => {
    if (!viewer || !osdModule || !width || !height) return;

    const overlayEl = document.createElement('div');
    overlayEl.id = 'pid-interactive-svg-overlay-container';
    overlayEl.style.width = '100%';
    overlayEl.style.height = '100%';
    overlayEl.style.position = 'absolute';
    overlayEl.style.top = '0';
    overlayEl.style.left = '0';
    // Pointer-events on container: 'none' in pan mode (to allow OSD pan), 'auto' in rescan/pen modes
    overlayEl.style.pointerEvents = traceToolRef.current === 'pan' ? 'none' : 'auto';

    const aspectRatio = height / width;
    const rect = new osdModule.Rect(0, 0, 1.0, aspectRatio);

    const attachOverlay = () => {
      // Re-assert DOM identity in case OSD cleared its overlays container.
      if (overlayEl.parentNode !== null) {
        overlayEl.parentNode.removeChild(overlayEl);
      }
      try {
        viewer.addOverlay({
          element: overlayEl,
          location: rect,
          checkResize: false,
        });
      } catch (e) {
        // viewer may be tearing down
      }
      // Restore tool-driven pointer-events after a re-attach.
      overlayEl.style.pointerEvents = traceToolRef.current === 'pan' ? 'none' : 'auto';
    };

    attachOverlay();
    setContainer(overlayEl);

    // Re-attach on every (re)open — this is what fixes the "tracing disappears
    // after switching tabs" regression.
    viewer.addHandler('open', attachOverlay);

    return () => {
      try {
        viewer.removeHandler('open', attachOverlay);
      } catch (e) {}
      try {
        viewer.removeOverlay(overlayEl);
      } catch (e) {
        // overlay may have been removed on viewer destroy
      }
      if (overlayEl.parentNode) {
        overlayEl.parentNode.removeChild(overlayEl);
      }
      setContainer(null);
    };
  }, [viewer, osdModule, width, height]);

  // Synchronize OpenSeadragon mouse navigation & overlay pointer-events based on tool
  useEffect(() => {
    if (!viewer) return;

    if (traceTool === 'rescan' || traceTool === 'pen') {
      viewer.setMouseNavEnabled(false);
      if (container) {
        container.style.pointerEvents = 'auto';
      }
    } else {
      // Pan mode
      viewer.setMouseNavEnabled(true);
      if (container) {
        container.style.pointerEvents = 'none';
      }
    }
  }, [viewer, traceTool, container]);

  // Listen to OpenSeadragon canvas-click to deselect when clicking empty space in pan mode
  useEffect(() => {
    if (!viewer) return;

    const onCanvasClick = (event: any) => {
      // If user clicked empty space without dragging and not in splitMode
      if (!splitMode && traceTool === 'pan') {
        onSelectRunIndices(new Set());
        setPopoverPos(null);
      }
    };

    viewer.addHandler('canvas-click', onCanvasClick);
    return () => {
      viewer.removeHandler('canvas-click', onCanvasClick);
    };
  }, [viewer, splitMode, traceTool, onSelectRunIndices]);

  // Coordinate conversion: Browser mouse -> SVG Drawing Pixel Coordinate
  const getImageCoordinates = useCallback(
    (e: React.MouseEvent | MouseEvent | React.PointerEvent): { x: number; y: number } | null => {
      if (!svgRef.current) return null;
      const svg = svgRef.current;
      const pt = svg.createSVGPoint();
      pt.x = e.clientX;
      pt.y = e.clientY;
      const ctm = svg.getScreenCTM();
      if (!ctm) return null;
      const transformed = pt.matrixTransform(ctm.inverse());
      return { x: transformed.x, y: transformed.y };
    },
    []
  );

  // Compute projection of point P onto segment AB
  const projectPointToSegment = (
    px: number,
    py: number,
    x0: number,
    y0: number,
    x1: number,
    y1: number
  ): { x: number; y: number; dist: number } => {
    const dx = x1 - x0;
    const dy = y1 - y0;
    const lenSq = dx * dx + dy * dy;
    if (lenSq < 1e-6) {
      return { x: x0, y: y0, dist: Math.hypot(px - x0, py - y0) };
    }
    const t = Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / lenSq));
    const projX = x0 + t * dx;
    const projY = y0 + t * dy;
    return { x: projX, y: projY, dist: Math.hypot(px - projX, py - projY) };
  };

  // Find closest projection point along a polyline
  const findClosestPointOnRun = (run: PipeRun, px: number, py: number) => {
    let minD = Infinity;
    let bestProj = { x: px, y: py };
    const pts = run.points;
    for (let i = 0; i < pts.length - 1; i++) {
      const proj = projectPointToSegment(px, py, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
      if (proj.dist < minD) {
        minD = proj.dist;
        bestProj = { x: proj.x, y: proj.y };
      }
    }
    return { ...bestProj, dist: minD };
  };

  // --- Draggable popover handlers ---------------------------------------
  // Grab the popover by its header and drag it anywhere on the canvas so it
  // never blocks the tiny line the user wants to edit underneath it.
  const handlePopoverPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const rect = viewer?.element?.getBoundingClientRect();
    if (!rect || !popoverPos) return;
    e.preventDefault();
    e.stopPropagation();
    popoverDragRef.current = {
      offsetX: e.clientX - rect.left - popoverPos.x,
      offsetY: e.clientY - rect.top - popoverPos.y,
      moved: false,
    };
    setPopoverDragging(true);
  };

  useEffect(() => {
    if (!popoverDragging) return;
    const onMove = (e: PointerEvent) => {
      const drag = popoverDragRef.current;
      const rect = viewer?.element?.getBoundingClientRect();
      if (!drag || !rect) return;
      const rawX = e.clientX - rect.left - drag.offsetX;
      const rawY = e.clientY - rect.top - drag.offsetY;
      drag.moved = true;
      setPopoverPos((prev) => {
        if (!prev) return prev;
        const clampedX = Math.max(8, Math.min(rect.width - 120, rawX));
        const clampedY = Math.max(8, Math.min(rect.height - 48, rawY));
        return { ...prev, x: clampedX, y: clampedY };
      });
    };
    const onUp = () => {
      setPopoverDragging(false);
      popoverDragRef.current = null;
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [popoverDragging, viewer]);

  // Line click handler with Shift+Click multi-select and dynamic popover placement
  const handleLineClick = (idx: number, e: React.MouseEvent | React.PointerEvent) => {
    e.stopPropagation();

    // If split mode is active and this is the selected line, execute split
    if (splitMode && selectedRunIndices.has(idx) && splitPreview) {
      handleExecuteSplit(idx, splitPreview.x, splitPreview.y);
      return;
    }

    if (traceTool !== 'pan') return;

    const rect = viewer?.element?.getBoundingClientRect();
    const imgCoords = getImageCoordinates(e);
    if (rect) {
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const clampedX = Math.max(16, Math.min(rect.width - 340, px - 150));
      const clampedY = Math.max(16, Math.min(rect.height - 240, py - 130));
      setPopoverPos({ x: clampedX, y: clampedY, imgX: imgCoords?.x ?? 0, imgY: imgCoords?.y ?? 0 });
    }

    if (e.shiftKey) {
      const next = new Set(selectedRunIndices);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      onSelectRunIndices(next);
    } else {
      onSelectRunIndices(new Set([idx]));
    }
  };

  // Execute line splitting
  const handleExecuteSplit = async (runIdx: number, x: number, y: number) => {
    if (splitting) return;
    setSplitting(true);
    try {
      await onSplitRun(runIdx, x, y);
      setSplitPreview(null);
      onSetSplitMode(false);
      setPopoverPos(null);
    } finally {
      setSplitting(false);
    }
  };

  // Recolor action for currently selected runs
  const handleApplyColor = (colorHex: string) => {
    if (selectedRunIndices.size === 0) return;
    onRecolorRuns(Array.from(selectedRunIndices), colorHex);
  };

  // Delete selected runs
  const handleDeleteSelected = async () => {
    if (!onDeleteRuns || selectedRunIndices.size === 0 || deleting) return;
    setDeleting(true);
    try {
      await onDeleteRuns(Array.from(selectedRunIndices));
      onSelectRunIndices(new Set());
      setPopoverPos(null);
      onSetSplitMode(false);
    } finally {
      setDeleting(false);
    }
  };

  // Save tag label
  const handleSaveTag = async () => {
    if (singleSelectedIdx === null || !onUpdateRunLabel || savingTag) return;
    setSavingTag(true);
    try {
      await onUpdateRunLabel(singleSelectedIdx, tagInput.trim());
    } finally {
      setSavingTag(false);
    }
  };

  const computeRunLength = (run: PipeRun | null | undefined) => {
    if (!run || !run.points || run.points.length < 2) return 0;
    let len = 0;
    for (let i = 0; i < run.points.length - 1; i++) {
      len += Math.hypot(run.points[i + 1][0] - run.points[i][0], run.points[i + 1][1] - run.points[i][1]);
    }
    return len;
  };

  const singleSelectedIdx = selectedRunIndices.size === 1 ? Array.from(selectedRunIndices)[0] : null;
  const singleSelectedRun = singleSelectedIdx !== null ? runs[singleSelectedIdx] : null;
  const associatedPid =
    singleSelectedIdx !== null ? pipingIds.find((p) => p.run_idx === singleSelectedIdx)?.pid : null;

  // Sync tag input with selected run
  useEffect(() => {
    if (singleSelectedRun) {
      setTagInput(singleSelectedRun.label || associatedPid || '');
    } else {
      setTagInput('');
    }
  }, [singleSelectedRun, associatedPid]);

  // -------------------------------------------------------------
  // VERTEX DRAGGING HANDLER (With Snap-to-Axis Alignment Assist)
  // -------------------------------------------------------------
  const handleStartVertexDrag = (runIdx: number, ptIdx: number, e: React.PointerEvent) => {
    e.stopPropagation();
    if (!viewer) return;

    // Temporarily disable OpenSeadragon navigation while dragging vertex
    viewer.setMouseNavEnabled(false);
    setDraggingVertex({ runIdx, ptIdx });

    const currentRun = runs[runIdx];
    if (!currentRun || !currentRun.points) return;
    const initialPoints: [number, number][] = currentRun.points.map((p) => [p[0], p[1]]);
    setLiveDragPoints(initialPoints);

    const onPointerMove = (moveEvent: PointerEvent) => {
      const coords = getImageCoordinates(moveEvent);
      if (!coords) return;

      let newX = Math.round(coords.x);
      let newY = Math.round(coords.y);
      let activeSnap: { axis: 'h' | 'v'; val: number } | null = null;

      // Snap assist with adjacent vertices (H/V alignment within 8px)
      if (ptIdx > 0) {
        const prev = initialPoints[ptIdx - 1];
        if (Math.abs(newX - prev[0]) <= 8) {
          newX = prev[0];
          activeSnap = { axis: 'v', val: newX };
        }
        if (Math.abs(newY - prev[1]) <= 8) {
          newY = prev[1];
          activeSnap = { axis: 'h', val: newY };
        }
      }
      if (ptIdx < initialPoints.length - 1) {
        const next = initialPoints[ptIdx + 1];
        if (Math.abs(newX - next[0]) <= 8) {
          newX = next[0];
          activeSnap = { axis: 'v', val: newX };
        }
        if (Math.abs(newY - next[1]) <= 8) {
          newY = next[1];
          activeSnap = { axis: 'h', val: newY };
        }
      }

      setSnapGuide(activeSnap);

      const updated = initialPoints.map((p, idx): [number, number] =>
        idx === ptIdx ? [newX, newY] : [p[0], p[1]]
      );
      setLiveDragPoints(updated);
    };

    const onPointerUp = async () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);

      setSnapGuide(null);
      setDraggingVertex(null);

      // Re-enable OpenSeadragon navigation
      if (viewer && traceTool === 'pan') {
        viewer.setMouseNavEnabled(true);
      }

      setLiveDragPoints((finalPts) => {
        if (finalPts && onUpdateRunPoints) {
          onUpdateRunPoints(runIdx, finalPts);
        }
        return null;
      });
    };

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
  };

  // -------------------------------------------------------------
  // BOX TRACE (RESCAN) HANDLER
  // -------------------------------------------------------------
  const handleSvgMouseDown = (e: React.MouseEvent) => {
    if (traceTool === 'rescan') {
      e.stopPropagation();
      const point = getImageCoordinates(e);
      if (point) {
        setRoiStart(point);
        setRoiRect({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
      }
    } else if (traceTool === 'pen') {
      e.stopPropagation();
      let point = getImageCoordinates(e);
      if (!point) return;

      let ptX = Math.round(point.x);
      let ptY = Math.round(point.y);

      // Magnet snap to nearest existing run endpoint within 18px
      let snapped = false;
      for (const r of runs) {
        if (!r.points || r.points.length < 2) continue;
        const p0 = r.points[0];
        const pn = r.points[r.points.length - 1];
        if (Math.hypot(ptX - p0[0], ptY - p0[1]) <= 18) {
          ptX = p0[0];
          ptY = p0[1];
          snapped = true;
          break;
        }
        if (Math.hypot(ptX - pn[0], ptY - pn[1]) <= 18) {
          ptX = pn[0];
          ptY = pn[1];
          snapped = true;
          break;
        }
      }
      setMagnetSnapped(snapped);

      // Shift key constraint: lock to horizontal or vertical relative to last placed point
      if (e.shiftKey && manualPoints.length > 0) {
        const last = manualPoints[manualPoints.length - 1];
        const dx = Math.abs(ptX - last[0]);
        const dy = Math.abs(ptY - last[1]);
        if (dx >= dy) {
          ptY = last[1];
        } else {
          ptX = last[0];
        }
      }

      setManualPoints((prev) => [...prev, [ptX, ptY]]);
    }
  };

  const handleSvgMouseMove = (e: React.MouseEvent) => {
    // Split mode preview
    if (splitMode && selectedRunIndices.size === 1) {
      const selectedIdx = Array.from(selectedRunIndices)[0];
      const selectedRun = runs[selectedIdx];
      if (selectedRun) {
        const coords = getImageCoordinates(e);
        if (coords) {
          const proj = findClosestPointOnRun(selectedRun, coords.x, coords.y);
          if (proj.dist <= 120) {
            setSplitPreview({ x: Math.round(proj.x), y: Math.round(proj.y) });
          } else {
            setSplitPreview(null);
          }
        }
      }
      return;
    }

    // Box trace drag
    if (traceTool === 'rescan' && roiStart) {
      const point = getImageCoordinates(e);
      if (point) {
        setRoiRect({
          x1: Math.min(roiStart.x, point.x),
          y1: Math.min(roiStart.y, point.y),
          x2: Math.max(roiStart.x, point.x),
          y2: Math.max(roiStart.y, point.y),
        });
      }
      return;
    }

    // Manual pen cursor preview
    if (traceTool === 'pen') {
      const point = getImageCoordinates(e);
      if (point) {
        let ptX = Math.round(point.x);
        let ptY = Math.round(point.y);

        // Check magnet snap
        let snapped = false;
        for (const r of runs) {
          if (!r.points || r.points.length < 2) continue;
          const p0 = r.points[0];
          const pn = r.points[r.points.length - 1];
          if (Math.hypot(ptX - p0[0], ptY - p0[1]) <= 18) {
            ptX = p0[0];
            ptY = p0[1];
            snapped = true;
            break;
          }
          if (Math.hypot(ptX - pn[0], ptY - pn[1]) <= 18) {
            ptX = pn[0];
            ptY = pn[1];
            snapped = true;
            break;
          }
        }
        setMagnetSnapped(snapped);

        if (e.shiftKey && manualPoints.length > 0) {
          const last = manualPoints[manualPoints.length - 1];
          if (Math.abs(ptX - last[0]) >= Math.abs(ptY - last[1])) {
            ptY = last[1];
          } else {
            ptX = last[0];
          }
        }

        setPenHoverPt([ptX, ptY]);
      }
    }
  };

  const handleSvgMouseUp = async (e: React.MouseEvent) => {
    if (traceTool !== 'rescan' || !roiStart) return;
    e.stopPropagation();

    const point = getImageCoordinates(e);
    setRoiStart(null);
    if (!point) return;

    const bounds = {
      x1: Math.round(Math.min(roiStart.x, point.x)),
      y1: Math.round(Math.min(roiStart.y, point.y)),
      x2: Math.round(Math.max(roiStart.x, point.x)),
      y2: Math.round(Math.max(roiStart.y, point.y)),
    };

    if (bounds.x2 - bounds.x1 < 12 || bounds.y2 - bounds.y1 < 12) {
      setRoiRect(null);
      return;
    }

    setRoiRect(bounds);

    // If Shift held: power-user shortcut to auto-replace without prompt
    if (e.shiftKey) {
      setToolBusy(true);
      try {
        await onRescan(bounds, true);
      } finally {
        setRoiRect(null);
        setToolBusy(false);
      }
      return;
    }

    // Otherwise show Option (C) modal prompt (Replace vs Append vs Cancel)
    setRoiPendingModal(bounds);
  };

  const handleExecuteRescanModal = async (replaceExisting: boolean) => {
    if (!roiPendingModal) return;
    const bounds = roiPendingModal;
    setRoiPendingModal(null);
    setRoiRect(null);
    setToolBusy(true);
    try {
      await onRescan(bounds, replaceExisting);
    } finally {
      setToolBusy(false);
    }
  };

  const finishManual = async () => {
    if (manualPoints.length < 2 || toolBusy) return;
    setToolBusy(true);
    try {
      await onManualRun(manualPoints);
      setManualPoints([]);
      setPenHoverPt(null);
    } finally {
      setToolBusy(false);
    }
  };

  // Keyboard shortcuts listener for tool actions
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && traceTool === 'pen') {
        e.preventDefault();
        void finishManual();
      }
      if (e.key === 'Escape') {
        if (traceTool !== 'pan') {
          e.preventDefault();
          setRoiStart(null);
          setRoiRect(null);
          setRoiPendingModal(null);
          setManualPoints([]);
          setPenHoverPt(null);
          onSetTraceTool('pan');
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [traceTool, manualPoints, toolBusy, onSetTraceTool]);

  return (
    <>
      {/* 1. Interactive SVG Overlay inside OpenSeadragon Canvas */}
      {container &&
        createPortal(
          <svg
            ref={svgRef}
            data-pipe-interactive="true"
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            style={{
              width: '100%',
              height: '100%',
              position: 'absolute',
              top: 0,
              left: 0,
              pointerEvents: traceTool === 'pan' ? 'none' : 'all',
              cursor:
                traceTool === 'rescan'
                  ? 'crosshair'
                  : traceTool === 'pen'
                  ? 'crosshair'
                  : splitMode
                  ? 'crosshair'
                  : 'default',
              overflow: 'visible',
              display: showOverlay ? 'block' : 'none',
              opacity: opacity,
              transition: 'opacity 0.2s ease',
            }}
            onMouseDown={handleSvgMouseDown}
            onMouseMove={handleSvgMouseMove}
            onMouseUp={handleSvgMouseUp}
            onDoubleClick={(e) => {
              if (traceTool === 'pen') {
                e.stopPropagation();
                void finishManual();
              }
            }}
          >
            <defs>
              <filter id="hover-glow" x="-30%" y="-30%" width="160%" height="160%">
                <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="#38BDF8" floodOpacity="0.9" />
              </filter>
              <filter id="select-halo" x="-40%" y="-40%" width="180%" height="180%">
                <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor="#F59E0B" floodOpacity="0.95" />
              </filter>
            </defs>

            {/* Polyline Pipe Runs */}
            {runs.map((run, idx) => {
              if (!run.points || run.points.length < 2) return null;
              const isSelected = selectedRunIndices.has(idx);
              const isHovered = hoveredRunIdx === idx;
              const overrideColor = colorOverrideMap?.get(idx);
              const strokeColor = overrideColor || run.color || '#2563EB';
              const isDimmed = Boolean(dimUncolored && colorOverrideMap && !overrideColor);

              // If dragging vertices of this run, use the live drag points
              const activePoints =
                draggingVertex?.runIdx === idx && liveDragPoints ? liveDragPoints : run.points;
              const ptsStr = activePoints.map((p) => `${p[0]},${p[1]}`).join(' ');

              return (
                <g key={run.id || `run-${idx}`} className="group">
                  {/* Invisible wide stroke for easy clicking & hovering (pointerEvents: stroke) */}
                  <polyline
                    points={ptsStr}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={22}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    style={{
                      pointerEvents: 'stroke',
                      cursor: splitMode ? 'crosshair' : 'pointer',
                    }}
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      pointerDownRecordRef.current = { x: e.clientX, y: e.clientY, time: Date.now() };
                    }}
                    onPointerUp={(e) => {
                      e.stopPropagation();
                      if (pointerDownRecordRef.current) {
                        const dist = Math.hypot(
                          e.clientX - pointerDownRecordRef.current.x,
                          e.clientY - pointerDownRecordRef.current.y
                        );
                        if (dist < 6) {
                          handleLineClick(idx, e);
                        }
                      }
                    }}
                    onClick={(e) => handleLineClick(idx, e)}
                    onMouseEnter={() => setHoveredRunIdx(idx)}
                    onMouseLeave={() => setHoveredRunIdx(null)}
                  />

                  {/* Selection Background Halo */}
                  {isSelected && (
                    <polyline
                      points={ptsStr}
                      fill="none"
                      stroke="#FBBF24"
                      strokeWidth={8}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      filter="url(#select-halo)"
                      style={{ pointerEvents: 'none' }}
                    />
                  )}

                  {/* Visible Pipe Run Stroke */}
                  <polyline
                    points={ptsStr}
                    fill="none"
                    stroke={isSelected ? '#F59E0B' : strokeColor}
                    strokeWidth={isSelected ? 5.5 : isHovered ? 5.0 : 3.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeDasharray={isSelected ? '10 5' : undefined}
                    filter={isHovered && !isSelected ? 'url(#hover-glow)' : undefined}
                    opacity={isDimmed ? 0.18 : 1}
                    style={{
                      pointerEvents: 'none',
                      transition: draggingVertex ? 'none' : 'stroke 0.15s ease, stroke-width 0.15s ease, opacity 0.15s ease',
                    }}
                  />

                  {/* Draggable Vertex Control Points for Selected Lines */}
                  {isSelected && (
                    <g className="control-points">
                      {activePoints.map((pt, ptIdx) => {
                        const isEndpoint = ptIdx === 0 || ptIdx === activePoints.length - 1;
                        const isCurrentDrag =
                          draggingVertex?.runIdx === idx && draggingVertex?.ptIdx === ptIdx;

                        return (
                          <circle
                            key={`vertex-${idx}-${ptIdx}`}
                            cx={pt[0]}
                            cy={pt[1]}
                            r={isCurrentDrag ? 9 : isEndpoint ? 7.5 : 5.5}
                            fill={isCurrentDrag ? '#EF4444' : isEndpoint ? '#F59E0B' : '#3B82F6'}
                            stroke="#FFFFFF"
                            strokeWidth={2.5}
                            style={{
                              pointerEvents: 'all',
                              cursor: isCurrentDrag ? 'grabbing' : 'grab',
                              transition: isCurrentDrag ? 'none' : 'r 0.12s ease',
                            }}
                            onPointerDown={(e) => {
                              handleStartVertexDrag(idx, ptIdx, e);
                            }}
                          />
                        );
                      })}
                    </g>
                  )}
                </g>
              );
            })}

            {/* Snap Guide Line during vertex dragging */}
            {snapGuide && (
              <line
                x1={snapGuide.axis === 'v' ? snapGuide.val : 0}
                y1={snapGuide.axis === 'h' ? snapGuide.val : 0}
                x2={snapGuide.axis === 'v' ? snapGuide.val : width}
                y2={snapGuide.axis === 'h' ? snapGuide.val : height}
                stroke="#10B981"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                pointerEvents="none"
              />
            )}

            {/* Box Trace Rubber-band Rectangle */}
            {roiRect && traceTool === 'rescan' && (
              <rect
                x={Math.min(roiRect.x1, roiRect.x2)}
                y={Math.min(roiRect.y1, roiRect.y2)}
                width={Math.abs(roiRect.x2 - roiRect.x1)}
                height={Math.abs(roiRect.y2 - roiRect.y1)}
                fill="#38BDF8"
                fillOpacity="0.15"
                stroke="#0284C7"
                strokeWidth="4"
                strokeDasharray="10 6"
                pointerEvents="none"
              />
            )}

            {/* Manual Pen Live Drawing Preview */}
            {traceTool === 'pen' && manualPoints.length > 0 && (
              <g pointerEvents="none">
                <polyline
                  points={
                    penHoverPt
                      ? [...manualPoints, penHoverPt].map((p) => p.join(',')).join(' ')
                      : manualPoints.map((p) => p.join(',')).join(' ')
                  }
                  fill="none"
                  stroke="#2563EB"
                  strokeWidth="4.5"
                  strokeDasharray="8 6"
                />
                {manualPoints.map((p, i) => (
                  <circle
                    key={`manual-pt-${i}`}
                    cx={p[0]}
                    cy={p[1]}
                    r={6}
                    fill="#2563EB"
                    stroke="#FFFFFF"
                    strokeWidth={2}
                  />
                ))}
                {penHoverPt && (
                  <circle
                    cx={penHoverPt[0]}
                    cy={penHoverPt[1]}
                    r={magnetSnapped ? 8 : 5}
                    fill={magnetSnapped ? '#10B981' : '#38BDF8'}
                    stroke="#FFFFFF"
                    strokeWidth={2}
                  />
                )}
              </g>
            )}

            {/* Split Mode Interactive Cut Point Marker */}
            {splitMode && splitPreview && (
              <g pointerEvents="none">
                <circle
                  cx={splitPreview.x}
                  cy={splitPreview.y}
                  r={10}
                  fill="#EF4444"
                  fillOpacity={0.85}
                  stroke="#FFFFFF"
                  strokeWidth={2.5}
                />
                <line
                  x1={splitPreview.x - 16}
                  y1={splitPreview.y}
                  x2={splitPreview.x + 16}
                  y2={splitPreview.y}
                  stroke="#FFFFFF"
                  strokeWidth={2}
                />
                <line
                  x1={splitPreview.x}
                  y1={splitPreview.y - 16}
                  x2={splitPreview.x}
                  y2={splitPreview.y + 16}
                  stroke="#FFFFFF"
                  strokeWidth={2}
                />
                <text
                  x={splitPreview.x + 16}
                  y={splitPreview.y - 14}
                  fill="#FFFFFF"
                  fontSize="22"
                  fontWeight="bold"
                  stroke="#000000"
                  strokeWidth="3"
                  paintOrder="stroke"
                >
                  ✂ Potong ({splitPreview.x}, {splitPreview.y})
                </text>
              </g>
            )}
          </svg>,
          container
        )}

      {/* 2. Floating Action Popover positioned dynamically near the clicked line */}
      {selectedRunIndices.size > 0 && showOverlay && (
        <div
          className="absolute z-40 bg-white/95 backdrop-blur border border-slate-200 rounded-2xl shadow-2xl p-3 flex flex-col space-y-2.5 text-xs animate-in fade-in zoom-in-95 duration-150"
          onClick={(e) => e.stopPropagation()}
          style={{
            left: popoverPos?.x ?? 24,
            top: popoverPos?.y ?? 24,
            minWidth: 280,
            maxWidth: 340,
            cursor: popoverDragging ? 'grabbing' : undefined,
          }}
        >
          {/* Popover Header: Info + Close (header doubles as drag handle) */}
          <div
            className={`flex items-center justify-between border-b border-slate-100 pb-2 select-none ${
              popoverDragging ? 'cursor-grabbing' : 'cursor-grab'
            }`}
            onPointerDown={handlePopoverPointerDown}
            title="Tahan & geser untuk memindahkan panel"
          >
            <div className="flex items-center space-x-2">
              <GripVertical className="w-3.5 h-3.5 text-slate-300 shrink-0" />
              {singleSelectedIdx !== null ? (
                <div className="flex items-center space-x-1.5 font-semibold text-slate-800">
                  <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
                  <span>Pipa #{singleSelectedIdx}</span>
                  <span className="text-[10px] text-slate-400 font-normal">
                    ({Math.round(computeRunLength(singleSelectedRun))} px)
                  </span>
                </div>
              ) : (
                <div className="font-semibold text-amber-900 flex items-center space-x-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                  <span>{selectedRunIndices.size} Pipa Terpilih</span>
                </div>
              )}
            </div>
            <button
              onClick={() => {
                onSelectRunIndices(new Set());
                setPopoverPos(null);
                onSetSplitMode(false);
              }}
              onPointerDown={(e) => e.stopPropagation()}
              className="p-1 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition"
              title="Tutup (Esc)"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Quick Color Palette */}
          <div className="space-y-1">
            <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
              Warna Garis
            </span>
            <div className="flex items-center space-x-2">
              {QUICK_COLORS.map((c) => (
                <button
                  key={c.hex}
                  onClick={() => handleApplyColor(c.hex)}
                  className="w-5 h-5 rounded-full border border-black/15 hover:scale-125 transition-transform shadow-sm focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-indigo-500"
                  style={{ backgroundColor: c.hex }}
                  title={`${c.name} (${c.hex})`}
                />
              ))}
              <label
                className="w-5 h-5 rounded-full border border-slate-300 flex items-center justify-center cursor-pointer hover:scale-110 transition-transform overflow-hidden relative"
                title="Pilih warna bebas (Hex)"
              >
                <Palette className="w-3 h-3 text-slate-600" />
                <input
                  type="color"
                  value={customColor}
                  onChange={(e) => {
                    setCustomColor(e.target.value);
                    handleApplyColor(e.target.value);
                  }}
                  className="opacity-0 absolute inset-0 cursor-pointer"
                />
              </label>
            </div>
          </div>

          {/* Single Selection Details & Actions */}
          {singleSelectedIdx !== null && (
            <>
              {/* Rename / Tag Input */}
              <div className="space-y-1">
                <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Tag / Label Line
                </span>
                <div className="flex items-center space-x-1.5">
                  <div className="relative flex-1">
                    <Tag className="w-3 h-3 text-slate-400 absolute left-2 top-2.5" />
                    <input
                      type="text"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSaveTag()}
                      placeholder="e.g. 605-6-GR-029"
                      className="w-full pl-6 pr-2 py-1 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                  </div>
                  <button
                    onClick={handleSaveTag}
                    disabled={savingTag}
                    className="p-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg transition"
                    title="Simpan Tag Pipa"
                  >
                    <Check className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Action Buttons: Split Line + Delete Line */}
              <div className="pt-1 flex items-center space-x-2">
                <button
                  onClick={() => onSetSplitMode(!splitMode)}
                  className={`flex-1 py-1.5 px-2.5 rounded-lg text-xs font-semibold flex items-center justify-center space-x-1.5 transition ${
                    splitMode
                      ? 'bg-red-600 text-white shadow-md animate-pulse'
                      : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
                  }`}
                  title="Potong polyline menjadi 2 bagian"
                >
                  <Scissors className="w-3.5 h-3.5" />
                  <span>{splitMode ? 'Batal Potong' : 'Split Line'}</span>
                </button>

                {onDeleteRuns && (
                  <button
                    onClick={handleDeleteSelected}
                    disabled={deleting}
                    className="py-1.5 px-2.5 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg text-xs font-semibold flex items-center space-x-1 transition"
                    title="Hapus garis pipa ini"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Hapus</span>
                  </button>
                )}
              </div>
            </>
          )}

          {/* Multi-Selection Batch Actions */}
          {selectedRunIndices.size > 1 && onDeleteRuns && (
            <div className="pt-1 flex items-center space-x-2">
              <button
                onClick={handleDeleteSelected}
                disabled={deleting}
                className="w-full py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-semibold flex items-center justify-center space-x-1.5 shadow transition"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Hapus {selectedRunIndices.size} Pipa Terpilih</span>
              </button>
            </div>
          )}
        </div>
      )}

      {/* 3. Option (C) Re-scan Confirmation Modal */}
      {roiPendingModal && (
        <div
          className="absolute z-50 bg-white border border-slate-300 rounded-2xl shadow-2xl p-4 flex flex-col space-y-3 text-xs animate-in fade-in zoom-in-95 duration-150"
          style={{
            left: '50%',
            top: '40%',
            transform: 'translate(-50%, -50%)',
            minWidth: 320,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center space-x-2 border-b border-slate-100 pb-2">
            <Crop className="w-4 h-4 text-cyan-600" />
            <h3 className="font-bold text-slate-800 text-sm">Re-scan Area Terpilih</h3>
          </div>
          <p className="text-slate-600 text-xs leading-relaxed">
            Pilih tindakan untuk garis pipa yang ada di dalam kotak area seleksi ini:
          </p>
          <div className="flex flex-col space-y-2 pt-1">
            <button
              onClick={() => handleExecuteRescanModal(true)}
              className="w-full py-2 px-3 bg-cyan-600 hover:bg-cyan-700 text-white font-semibold rounded-xl flex items-center justify-center space-x-2 shadow transition"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Ganti Pipa Lama di Area Ini (Replace)</span>
            </button>
            <button
              onClick={() => handleExecuteRescanModal(false)}
              className="w-full py-2 px-3 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold rounded-xl flex items-center justify-center space-x-2 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Tambahkan Pipa Baru Saja (Append)</span>
            </button>
            <button
              onClick={() => {
                setRoiPendingModal(null);
                setRoiRect(null);
              }}
              className="w-full py-1.5 text-slate-400 hover:text-slate-600 text-center font-medium transition"
            >
              Batal
            </button>
          </div>
        </div>
      )}

      {/* 4. Split Mode Guide Banner */}
      {splitMode && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-red-600/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 animate-bounce">
          <Scissors className="w-3.5 h-3.5" />
          <span>Arahkan kursor ke garis pipa lalu klik untuk memotong (split)</span>
          <button
            onClick={() => onSetSplitMode(false)}
            className="ml-2 bg-red-700 hover:bg-red-800 px-2 py-0.5 rounded text-[11px]"
          >
            Batal
          </button>
        </div>
      )}

      {/* 5. Tool Helper Hints Bar */}
      {traceTool === 'pen' && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-blue-700/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2">
          <Pencil className="w-3.5 h-3.5" />
          <span>
            {manualPoints.length === 0
              ? 'Klik pada kanvas untuk mulai titik pipa'
              : `${manualPoints.length} titik diletakkan. Tahan [Shift] untuk lurus H/V. [Enter] selesai, [Esc] batal.`}
          </span>
          {manualPoints.length >= 2 && (
            <button
              onClick={finishManual}
              disabled={toolBusy}
              className="ml-2 bg-emerald-500 hover:bg-emerald-600 px-2.5 py-0.5 rounded text-[11px] font-bold transition"
            >
              ✓ Selesai
            </button>
          )}
        </div>
      )}

      {traceTool === 'rescan' && !roiPendingModal && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-cyan-700/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2">
          <Crop className="w-3.5 h-3.5" />
          <span>Tarik kotak (drag rectangle) pada area pipa yang ingin di-scan ulang. Tahan [Shift] untuk auto-replace.</span>
        </div>
      )}

      {/* 6. Active Tool Dock / Pill Toolbar at Bottom-Center */}
      <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-1 rounded-2xl bg-white/95 backdrop-blur-md p-1.5 shadow-2xl border border-slate-200">
        <button
          onClick={() => {
            onSetTraceTool('pan');
            setManualPoints([]);
            setRoiRect(null);
            setRoiPendingModal(null);
          }}
          className={`px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition ${
            traceTool === 'pan'
              ? 'bg-indigo-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Pan & Select Tool: Geser kanvas atau klik garis pipa untuk edit"
        >
          <Hand className="w-3.5 h-3.5" />
          <span>Pan & Select</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('rescan');
            setManualPoints([]);
          }}
          className={`px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition ${
            traceTool === 'rescan'
              ? 'bg-cyan-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Box Trace (ROI): Tarik kotak untuk deteksi ulang area tertentu"
        >
          <Crop className="w-3.5 h-3.5" />
          <span>Box Trace</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('pen');
            setRoiRect(null);
            setRoiPendingModal(null);
          }}
          className={`px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition ${
            traceTool === 'pen'
              ? 'bg-blue-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Manual Pen: Gambar garis pipa baru secara manual"
        >
          <Pencil className="w-3.5 h-3.5" />
          <span>Manual Pen</span>
        </button>

        {traceTool === 'pen' && manualPoints.length >= 2 && (
          <button
            disabled={toolBusy}
            onClick={finishManual}
            className="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold transition flex items-center space-x-1 shadow"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Selesai (Enter)</span>
          </button>
        )}

        {toolBusy && (
          <span className="px-2.5 text-[11px] font-medium text-indigo-600 animate-pulse">
            Memproses...
          </span>
        )}
      </div>
    </>
  );
}
