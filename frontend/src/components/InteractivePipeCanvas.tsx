'use client';

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
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
  GripVertical,
  BoxSelect,
  Wand2,
  Eye,
  EyeOff,
} from 'lucide-react';
import { ManualGroup, PipeRun, PipingID } from '@/types/schema';
import { useCanvasOverlay, applyMouseNav } from '@/hooks/useCanvasOverlay';

// Liang-Barsky/Cohen-Sutherland style test: does segment (x0,y0)-(x1,y1) intersect an
// axis-aligned rectangle? Used by the Multi-Select marquee so long pipes crossing the
// box are selected even when none of their vertices fall inside it.
function segmentIntersectsRect(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  rect: { x1: number; y1: number; x2: number; y2: number },
): boolean {
  let t0 = 0;
  let t1 = 1;
  const dx = x1 - x0;
  const dy = y1 - y0;
  const p = [-dx, dx, -dy, dy];
  const q = [x0 - rect.x1, rect.x2 - x0, y0 - rect.y1, rect.y2 - y0];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false; // parallel and outside
    } else {
      const r = q[i] / p[i];
      if (p[i] < 0) {
        if (r > t1) return false;
        if (r > t0) t0 = r;
      } else {
        if (r < t0) return false;
        if (r < t1) t1 = r;
      }
    }
  }
  return true;
}

// 5 Quick Colors: Biru #2563EB, Hijau #10B981, Merah #EF4444, Kuning #F59E0B, Ungu #8B5CF6
export const QUICK_COLORS = [
  { name: 'Biru Netral (Default)', hex: '#2563EB' },
  { name: 'Hijau (Process)', hex: '#10B981' },
  { name: 'Merah (Corrosion)', hex: '#EF4444' },
  { name: 'Kuning (Hazard)', hex: '#F59E0B' },
  { name: 'Ungu (Spec Break)', hex: '#8B5CF6' },
  { name: 'Biru Muda', hex: '#0EA5E9' },
  { name: 'Teal', hex: '#14B8A6' },
  { name: 'Pink', hex: '#EC4899' },
  { name: 'Lime', hex: '#84CC16' },
  { name: 'Indigo', hex: '#6366F1' },
  { name: 'Slate', hex: '#64748B' },
];

function runMidpoint(points: [number, number][]) {
  const segments = points.slice(1).map((point, index) => ({
    from: points[index],
    to: point,
    length: Math.hypot(point[0] - points[index][0], point[1] - points[index][1]),
  }));
  const total = segments.reduce((sum, segment) => sum + segment.length, 0);
  if (total <= 0) return points[0];
  let remaining = total / 2;
  for (const segment of segments) {
    if (remaining <= segment.length) {
      const ratio = segment.length === 0 ? 0 : remaining / segment.length;
      return [
        segment.from[0] + (segment.to[0] - segment.from[0]) * ratio,
        segment.from[1] + (segment.to[1] - segment.from[1]) * ratio,
      ] as [number, number];
    }
    remaining -= segment.length;
  }
  return points[points.length - 1];
}

interface InteractivePipeCanvasProps {
  viewer: any;
  osdModule: any;
  width: number;
  height: number;
  runs: PipeRun[];
  manualGroups: ManualGroup[];
  onUpdateGroupStamp: (groupId: string, position: { x: number; y: number }) => Promise<void>;
  pipingIds: PipingID[];
  showOverlay: boolean;
  opacity: number;
  selectedRunIndices: Set<number>;
  onSelectRunIndices: (indices: Set<number>) => void;
  onMarkRun: (runIdx: number) => Promise<void>;
  onRecolorRuns: (runIdxs: number[], newColor: string) => void;
  onUpdateRunLineStyle: (runIdxs: number[], style: 'solid' | 'dashed') => void;
  onSplitRun: (runIdx: number, x: number, y: number) => Promise<void>;
  onDeleteRuns?: (runIdxs: number[]) => Promise<void>;
  onUpdateRunLabel?: (runIdx: number, label: string) => Promise<void>;
  onUpdateRunPoints?: (runIdx: number, points: [number, number][]) => Promise<void>;
  splitMode: boolean;
  onSetSplitMode: (active: boolean) => void;
  traceTool: 'pan' | 'rescan' | 'pen' | 'multiselect' | 'wand';
  onSetTraceTool: (tool: 'pan' | 'rescan' | 'pen' | 'multiselect' | 'wand') => void;
  onRescan: (bounds: { x1: number; y1: number; x2: number; y2: number }) => Promise<void>;
  onManualRun: (points: [number, number][]) => Promise<void>;
  // Optional mode-driven color override: maps run index -> CSS color.
  // Used for Corrosion System / Circuit views so circuit coloring is rendered
  // as a vector layer on the raw CAD image instead of swapping to a server PNG.
  colorOverrideMap?: Map<number, string> | null;
  hiddenRunIndices?: Set<number>;
  dimUncolored?: boolean;
}

export default function InteractivePipeCanvas({
  viewer,
  osdModule,
  width,
  height,
  runs,
  manualGroups,
  onUpdateGroupStamp,
  pipingIds,
  showOverlay,
  opacity,
  selectedRunIndices,
  onSelectRunIndices,
  onMarkRun,
  onRecolorRuns,
  onUpdateRunLineStyle,
  onSplitRun,
  onDeleteRuns,
  onUpdateRunLabel,
  onUpdateRunPoints,
  splitMode,
  onSetSplitMode,
  traceTool,
  onSetTraceTool,
  onRescan,
  onManualRun,
  colorOverrideMap,
  hiddenRunIndices,
  dimUncolored,
}: InteractivePipeCanvasProps) {
  const [showDetectedLines, setShowDetectedLines] = useState(true);
  const [hoveredRunIdx, setHoveredRunIdx] = useState<number | null>(null);
  const [stampDrag, setStampDrag] = useState<{
    groupId: string;
    startX: number;
    startY: number;
    pointerOffsetX: number;
    pointerOffsetY: number;
    x: number;
    y: number;
    moved: boolean;
  } | null>(null);
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

  // Multi-select marquee state (drag a blue rectangle to select many runs)
  const [marqueeRect, setMarqueeRect] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  // Refs mirror the marquee state so the mouseup handler never reads a stale closure.
  const marqueeStartRef = useRef<{ x: number; y: number } | null>(null);
  const marqueeRectRef = useRef<{ x1: number; y1: number; x2: number; y2: number } | null>(null);

  const [toolBusy, setToolBusy] = useState(false);
  // Temporary hand-pan: true while Ctrl/Cmd or Space is held down. Lets the user
  // pan the canvas with the mouse without switching the active tool.

  // Arrow-key micro-nudge: accumulated [dx, dy] offset per selected run index. Applied
  // at render-time for instant feedback; committed to the backend (debounced) on idle.
  const [nudgeOffsets, setNudgeOffsets] = useState<Map<number, [number, number]>>(new Map());
  // Hover and toolbar state change often; pipe geometry changes only after an edit.
  const pointStrings = useMemo(
    () => runs.map((run) => run.points?.map((point) => `${point[0]},${point[1]}`).join(' ') ?? ''),
    [runs],
  );
  const nudgeCommitRef = useRef<Map<number, [number, number]>>(new Map());
  const nudgeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const pointerDownRecordRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const markingRunIdsRef = useRef<Set<string | number>>(new Set());
  const { container, tempPan } = useCanvasOverlay(viewer, osdModule, width, height, traceTool);

  // Commit accumulated nudge offset for one run via onUpdateRunPoints.
  const commitNudge = useCallback(
    (runIdx: number, dx: number, dy: number) => {
      if (!onUpdateRunPoints) return;
      const run = runs[runIdx];
      if (!run || !run.points) return;
      const pts = run.points.map(
        (p) => [Math.round(p[0] + dx), Math.round(p[1] + dy)] as [number, number]
      );
      void onUpdateRunPoints(runIdx, pts);
    },
    [onUpdateRunPoints, runs]
  );

  // Arrow-key micro-nudge: move every vertex of the selected run(s) by 1px
  // (5px with Shift). Instant on-canvas feedback + debounced persistence.
  useEffect(() => {
    const handleNudge = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      ) {
        return;
      }
      const isArrow =
        e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowRight';
      if (!isArrow || selectedRunIndices.size === 0) return;
      e.preventDefault();
      const step = e.shiftKey ? 5 : 1;
      const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
      const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;

      const next = new Map(nudgeOffsets);
      const commits = nudgeCommitRef.current;
      for (const idx of selectedRunIndices) {
        const prevOff = next.get(idx) || [0, 0];
        const newOff: [number, number] = [prevOff[0] + dx, prevOff[1] + dy];
        next.set(idx, newOff);
        commits.set(idx, newOff);
      }
      setNudgeOffsets(next);

      // Debounced commit: flush once the user stops tapping for 260ms.
      if (nudgeTimerRef.current) clearTimeout(nudgeTimerRef.current);
      nudgeTimerRef.current = setTimeout(() => {
        const pending = nudgeCommitRef.current;
        pending.forEach((off, idx) => {
          if (off[0] !== 0 || off[1] !== 0) commitNudge(idx, off[0], off[1]);
        });
        nudgeCommitRef.current = new Map();
        setNudgeOffsets(new Map());
      }, 260);
    };
    window.addEventListener('keydown', handleNudge);
    return () => {
      window.removeEventListener('keydown', handleNudge);
      if (nudgeTimerRef.current) clearTimeout(nudgeTimerRef.current);
    };
  }, [selectedRunIndices, nudgeOffsets, commitNudge]);

  // Clear pending nudges whenever the runs array or selection changes identity
  // (after commit/split/delete) so stale offsets never misalign a re-indexed run.
  useEffect(() => {
    setNudgeOffsets(new Map());
    nudgeCommitRef.current = new Map();
  }, [runs]);

  // Listen to OpenSeadragon canvas-click to deselect when clicking empty space in pan mode
  useEffect(() => {
    if (!viewer) return;

    const onCanvasClick = () => {
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

    const clickedRun = runs[idx];
    if (clickedRun?.marked === false) {
      const markKey = clickedRun.id || idx;
      if (markingRunIdsRef.current.has(markKey)) return;
      markingRunIdsRef.current.add(markKey);
      void onMarkRun(idx).finally(() => markingRunIdsRef.current.delete(markKey));
      return;
    }

    // If split mode is active and this is the selected line, execute split.
    // Prefer the live hover preview point; if the cursor hadn't produced one yet
    // (e.g. the pointer events only just started reaching the SVG), fall back to
    // projecting the click coordinates directly onto the line.
    if (splitMode && selectedRunIndices.has(idx)) {
      let cut = splitPreview;
      if (!cut) {
        const coords = getImageCoordinates(e);
        const run = runs[idx];
        if (coords && run) {
          const proj = findClosestPointOnRun(run, coords.x, coords.y);
          cut = { x: Math.round(proj.x), y: Math.round(proj.y) };
        }
      }
      if (cut) {
        handleExecuteSplit(idx, cut.x, cut.y);
        return;
      }
    }

    // Multi-select tool: click a line to toggle it in the selection set.
    if (traceTool === 'multiselect') {
      const next = new Set(selectedRunIndices);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      onSelectRunIndices(next);
      return;
    }

    if (traceTool !== 'pan' && traceTool !== 'wand') return;

    const rect = viewer?.element?.getBoundingClientRect();
    const imgCoords = getImageCoordinates(e);
    if (rect) {
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      // Keep the popover OFF the clicked line: place it just below-right of the cursor
      // (its top edge ~28px under the pointer). Flip above/left when the panel would
      // run off the canvas. This guarantees the line you clicked stays visible and grabbable.
      const PANEL_W = 320;
      const PANEL_H = 240;
      const GAP = 28;
      let x = px + GAP;
      let y = py + GAP;
      if (x + PANEL_W > rect.width - 12) x = px - GAP - PANEL_W; // flip to the left
      if (y + PANEL_H > rect.height - 12) y = py - GAP - PANEL_H; // flip above
      // Final clamp so the panel never leaves the viewport.
      x = Math.max(12, Math.min(rect.width - PANEL_W - 12, x));
      y = Math.max(12, Math.min(rect.height - 120, y));
      setPopoverPos({ x, y, imgX: imgCoords?.x ?? 0, imgY: imgCoords?.y ?? 0 });
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

  const handleApplyLineStyle = (style: 'solid' | 'dashed') => {
    if (selectedRunIndices.size === 0) return;
    onUpdateRunLineStyle(Array.from(selectedRunIndices), style);
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
    applyMouseNav(viewer, false);
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
        applyMouseNav(viewer, true);
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
  const handleSvgMouseDown = async (e: React.MouseEvent) => {
    if (traceTool === 'multiselect') {
      e.stopPropagation();
      const point = getImageCoordinates(e);
      if (point) {
        const start = { x: point.x, y: point.y };
        const rect = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
        marqueeStartRef.current = start;
        marqueeRectRef.current = rect;
        setMarqueeRect(rect);
      }
      return;
    }
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

    // Multi-select marquee drag
    if (traceTool === 'multiselect' && marqueeStartRef.current) {
      const point = getImageCoordinates(e);
      const start = marqueeStartRef.current;
      if (point) {
        const rect = {
          x1: Math.min(start.x, point.x),
          y1: Math.min(start.y, point.y),
          x2: Math.max(start.x, point.x),
          y2: Math.max(start.y, point.y),
        };
        marqueeRectRef.current = rect;
        setMarqueeRect(rect);
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
    if (traceTool === 'multiselect' && marqueeStartRef.current) {
      e.stopPropagation();
      const point = getImageCoordinates(e);
      const start = marqueeStartRef.current;
      marqueeStartRef.current = null;

      const bounds = point
        ? {
            x1: Math.min(start.x, point.x),
            y1: Math.min(start.y, point.y),
            x2: Math.max(start.x, point.x),
            y2: Math.max(start.y, point.y),
          }
        : marqueeRectRef.current;
      marqueeRectRef.current = null;
      setMarqueeRect(null);

      // A tiny drag (click-like) clears the current selection instead of selecting nothing.
      if (!bounds || bounds.x2 - bounds.x1 < 6 || bounds.y2 - bounds.y1 < 6) {
        onSelectRunIndices(new Set());
        setPopoverPos(null);
        return;
      }

      // Select every run whose polyline intersects the marquee:
      // a run is picked when ANY vertex lies inside, OR a segment crosses the box
      // (so long lines passing through a small box are still captured), OR the
      // segment midpoint is inside.
      const inside = (x: number, y: number) =>
        x >= bounds.x1 && x <= bounds.x2 && y >= bounds.y1 && y <= bounds.y2;
      const next = new Set<number>(e.shiftKey ? selectedRunIndices : []);
      runs.forEach((run, idx) => {
        if (hiddenRunIndices?.has(idx)) return;
        if (!run.points || run.points.length < 2) return;
        let hit = run.points.some((p) => inside(p[0], p[1]));
        if (!hit) {
          for (let i = 0; i < run.points.length - 1 && !hit; i++) {
            const a = run.points[i];
            const b = run.points[i + 1];
            const mx = (a[0] + b[0]) / 2;
            const my = (a[1] + b[1]) / 2;
            if (inside(mx, my)) hit = true;
            else if (segmentIntersectsRect(a[0], a[1], b[0], b[1], bounds)) hit = true;
          }
        }
        if (hit) next.add(idx);
      });

      onSelectRunIndices(next);
      if (next.size > 0) {
        // Anchor the action popover near the marquee so the user can delete in one click.
        const rect = viewer?.element?.getBoundingClientRect();
        if (rect) {
          const clampedX = Math.max(16, Math.min(rect.width - 340, (bounds.x2 + bounds.x1) / 2 - 150));
          const clampedY = Math.max(16, Math.min(rect.height - 240, bounds.y2 + 12));
          setPopoverPos({ x: clampedX, y: clampedY, imgX: 0, imgY: 0 });
        }
      } else {
        setPopoverPos(null);
      }
      return;
    }

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

    // Ignore accidental click-like drags (too small to be a real ROI box).
    if (bounds.x2 - bounds.x1 < 12 || bounds.y2 - bounds.y1 < 12) {
      setRoiRect(null);
      return;
    }

    // Keep the rubber-band rectangle visible while the request is in flight so the user
    // sees what is being scanned; the backend decides automatically whether to extend a
    // touched pipe (stitch), bridge two severed pipes, or append a brand-new run. There
    // is no more Replace/Append confirmation modal — releasing the drag executes directly.
    if (toolBusy) {
      setRoiRect(null);
      return;
    }
    setRoiRect(bounds);
    setToolBusy(true);
    try {
      await onRescan(bounds);
    } finally {
      setRoiRect(null);
      setToolBusy(false);
    }
  };

  const finishManual = useCallback(async () => {
    if (manualPoints.length < 2 || toolBusy) return;
    setToolBusy(true);
    try {
      await onManualRun(manualPoints);
      setManualPoints([]);
      setPenHoverPt(null);
    } finally {
      setToolBusy(false);
    }
  }, [manualPoints, toolBusy, onManualRun]);

  // Keyboard shortcuts listener for tool actions
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // GUARD: ignore keys typed inside form fields (tag rename input, textarea, etc.)
      // so Backspace/Enter/Space/Arrow keys never leak into canvas shortcuts.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === 'Enter' && traceTool === 'pen') {
        e.preventDefault();
        void finishManual();
      }
      if (e.key === 'Escape') {
        if (traceTool !== 'pan') {
          e.preventDefault();
          setRoiStart(null);
          setRoiRect(null);
          setManualPoints([]);
          setPenHoverPt(null);
          marqueeStartRef.current = null;
          setMarqueeRect(null);
          onSetTraceTool('pan');
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [traceTool, onSetTraceTool, finishManual]);

  // Render an individual pipe run (polyline + hit area + halo)
  const renderPipeRun = (run: PipeRun, idx: number, isSelected: boolean) => {
    if (hiddenRunIndices?.has(idx)) return null;
    if (run.marked === false && !showDetectedLines) return null;
    if (!run.points || run.points.length < 2) return null;
    const isHovered = hoveredRunIdx === idx;
    const isEquipOutline = Boolean(run.equipment_outline);
    const overrideColor = colorOverrideMap?.get(idx);
    const strokeColor = overrideColor || run.color || (isEquipOutline ? '#F97316' : '#2563EB');
    const isDimmed = Boolean(dimUncolored && colorOverrideMap && !overrideColor);

    // If dragging vertices of this run, use the live drag points
    const basePoints =
      draggingVertex?.runIdx === idx && liveDragPoints ? liveDragPoints : run.points;
    // Apply any in-flight arrow-key nudge offset for instant feedback.
    const nudge = nudgeOffsets.get(idx);
    const activePoints =
      nudge && (nudge[0] !== 0 || nudge[1] !== 0)
        ? basePoints.map((p) => [p[0] + nudge[0], p[1] + nudge[1]] as [number, number])
        : basePoints;
    const ptsStr = nudge || (draggingVertex?.runIdx === idx && liveDragPoints)
      ? activePoints.map((p) => `${p[0]},${p[1]}`).join(' ')
      : pointStrings[idx];

    return (
      <g key={run.id || `run-${idx}`} className="group">
        {/* Invisible wide stroke for easy clicking & hovering (pointerEvents: stroke).
            In Multi-Select mode the lines yield pointer events to the SVG surface so a
            drag anywhere (even over a line) paints the selection marquee instead. */}
        <polyline
          points={ptsStr}
          fill="none"
          stroke="transparent"
          strokeWidth={isSelected ? 26 : run.marked === false ? 20 : 22}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            pointerEvents: traceTool === 'multiselect' ? 'none' : 'stroke',
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
          stroke={isSelected ? '#F59E0B' : run.marked === false ? '#94A3B8' : strokeColor}
          strokeWidth={isSelected ? 5.5 : isHovered ? 5.0 : isEquipOutline ? 2.5 : 3.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={isSelected ? '10 5' : run.marked === false ? '5 6' : run.line_style === 'dashed' ? '8 6' : isEquipOutline ? '7 4' : undefined}
          filter={isHovered && !isSelected && run.marked !== false ? 'url(#hover-glow)' : undefined}
          opacity={isDimmed ? 0.18 : run.marked === false ? (isHovered ? 0.82 : 0.18) : isEquipOutline ? 0.95 : 1}
          style={{
            pointerEvents: 'none',
            transition: draggingVertex ? 'none' : 'stroke 0.15s ease, stroke-width 0.15s ease, opacity 0.15s ease',
          }}
        />
      </g>
    );
  };

  const renderGroupStamp = (group: ManualGroup) => {
    const kind = group.kind || 'circuit';
    const members = runs.filter((run) => run.marked === true && run.points?.length >= 2 && (
      kind === 'system'
        ? run.system_group_id === group.id || (!run.system_group_id && run.group_id === group.id)
        : run.circuit_group_id === group.id || (!run.circuit_group_id && run.group_id === group.id)
    ));
    if (members.length === 0) return null;

    const longest = members.reduce((best, run) => {
      const length = run.points.slice(1).reduce((sum, point, idx) =>
        sum + Math.hypot(point[0] - run.points[idx][0], point[1] - run.points[idx][1]), 0);
      const bestLength = best.points.slice(1).reduce((sum, point, idx) =>
        sum + Math.hypot(point[0] - best.points[idx][0], point[1] - best.points[idx][1]), 0);
      return length > bestLength ? run : best;
    });
    const midpoint = runMidpoint(longest.points);
    const boxWidth = Math.max(82, (group.name || '').length * 10 + 14);
    const defaultPosition = {
      x: Math.max(0, Math.min(width - boxWidth, midpoint[0] - boxWidth / 2)),
      y: Math.max(0, Math.min(height - 30, midpoint[1] - 50)),
    };
    const position = stampDrag?.groupId === group.id
      ? { x: stampDrag.x, y: stampDrag.y }
      : group.stampPosition || defaultPosition;
    const isDragging = stampDrag?.groupId === group.id;

    return (
      <g
        key={`stamp-${group.id}`}
        data-group-stamp={group.id}
        style={{ pointerEvents: 'all', cursor: isDragging ? 'grabbing' : 'grab', touchAction: 'none' }}
        onPointerDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const pointer = getImageCoordinates(e);
          if (!pointer) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          setStampDrag({
            groupId: group.id,
            startX: position.x,
            startY: position.y,
            pointerOffsetX: pointer.x - position.x,
            pointerOffsetY: pointer.y - position.y,
            x: position.x,
            y: position.y,
            moved: false,
          });
        }}
        onPointerMove={(e) => {
          if (stampDrag?.groupId !== group.id) return;
          e.preventDefault();
          e.stopPropagation();
          const pointer = getImageCoordinates(e);
          if (!pointer) return;
          const x = Math.max(0, Math.min(width - boxWidth, pointer.x - stampDrag.pointerOffsetX));
          const y = Math.max(0, Math.min(height - 30, pointer.y - stampDrag.pointerOffsetY));
          setStampDrag({
            ...stampDrag,
            x,
            y,
            moved: stampDrag.moved || Math.hypot(x - stampDrag.startX, y - stampDrag.startY) > 2,
          });
        }}
        onPointerUp={(e) => {
          if (stampDrag?.groupId !== group.id) return;
          e.preventDefault();
          e.stopPropagation();
          const pointer = getImageCoordinates(e);
          const x = pointer
            ? Math.max(0, Math.min(width - boxWidth, pointer.x - stampDrag.pointerOffsetX))
            : stampDrag.x;
          const y = pointer
            ? Math.max(0, Math.min(height - 30, pointer.y - stampDrag.pointerOffsetY))
            : stampDrag.y;
          if (stampDrag.moved) {
            void onUpdateGroupStamp(group.id, { x: Math.round(x), y: Math.round(y) });
          }
          setStampDrag(null);
        }}
        onPointerCancel={() => setStampDrag(null)}
      >
        <rect
          x={position.x}
          y={position.y}
          width={boxWidth}
          height={30}
          rx={2}
          fill="#FFFFFF"
          fillOpacity={0.88}
          stroke={group.color || '#2563EB'}
          strokeWidth={2}
        />
        <text
          x={position.x + 7}
          y={position.y + 21}
          fill={group.color || '#2563EB'}
          fontSize={17}
          fontWeight="600"
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          {group.name}
        </text>
      </g>
    );
  };

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
              // In split mode we must intercept pointer events on the traced lines
              // even though the active tool is 'pan', otherwise the OSD base canvas
              // swallows the mousemove that computes the cut preview and the click
              // that executes the split.
              pointerEvents: tempPan
                ? 'none'
                : splitMode && selectedRunIndices.size === 1
                ? 'all'
                : traceTool === 'pan'
                ? 'none'
                : 'all',
              cursor: tempPan
                ? 'grab'
                : traceTool === 'wand'
                ? 'crosshair'
                : traceTool === 'rescan'
                ? 'crosshair'
                : traceTool === 'pen'
                ? 'crosshair'
                : traceTool === 'multiselect'
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

            {/* 1. Unselected Pipe Runs (background layer) */}
            {runs.map((run, idx) => {
              if (run.marked === false && !showDetectedLines) return null;
              if (selectedRunIndices.has(idx)) return null;
              return renderPipeRun(run, idx, false);
            })}

            {/* 2. Selected Pipe Runs (elevated above unselected runs so halo and stroke take priority) */}
            {runs.map((run, idx) => {
              if (run.marked === false && !showDetectedLines) return null;
              if (!selectedRunIndices.has(idx)) return null;
              return renderPipeRun(run, idx, true);
            })}

            {/* 3. Draggable Vertex Control Points for Selected Lines (ALWAYS on top of all pipe runs) */}
            {runs.map((run, idx) => {
              if (hiddenRunIndices?.has(idx) || (run.marked === false && !showDetectedLines) || !selectedRunIndices.has(idx) || !run.points || run.points.length < 2) return null;
              const basePoints =
                draggingVertex?.runIdx === idx && liveDragPoints ? liveDragPoints : run.points;
              const nudge = nudgeOffsets.get(idx);
              const activePoints =
                nudge && (nudge[0] !== 0 || nudge[1] !== 0)
                  ? basePoints.map((p) => [p[0] + nudge[0], p[1] + nudge[1]] as [number, number])
                  : basePoints;

              return (
                <g key={`control-points-${run.id || idx}`} className="control-points">
                  {activePoints.map((pt, ptIdx) => {
                    const isEndpoint = ptIdx === 0 || ptIdx === activePoints.length - 1;
                    const isCurrentDrag =
                      draggingVertex?.runIdx === idx && draggingVertex?.ptIdx === ptIdx;

                    return (
                      <g
                        key={`vertex-${idx}-${ptIdx}`}
                        style={{ cursor: isCurrentDrag ? 'grabbing' : 'grab' }}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          e.preventDefault();
                          handleStartVertexDrag(idx, ptIdx, e);
                        }}
                      >
                        {/* Generous invisible hit target circle (radius 18) so clicking near the endpoint
                            ALWAYS grabs this vertex handle, preventing clicks from leaking to adjacent/touching lines */}
                        <circle
                          cx={pt[0]}
                          cy={pt[1]}
                          r={18}
                          fill="transparent"
                          style={{ pointerEvents: 'all' }}
                        />
                        {/* Visible styled handle circle */}
                        <circle
                          cx={pt[0]}
                          cy={pt[1]}
                          r={isCurrentDrag ? 9 : isEndpoint ? 7.5 : 5.5}
                          fill={isCurrentDrag ? '#EF4444' : isEndpoint ? '#F59E0B' : '#3B82F6'}
                          stroke="#FFFFFF"
                          strokeWidth={2.5}
                          style={{
                            pointerEvents: 'none',
                            transition: isCurrentDrag ? 'none' : 'r 0.12s ease',
                          }}
                        />
                      </g>
                    );
                  })}
                </g>
              );
            })}

            {/* Draggable corrosion group stamps sit above the pipe and selection layers. */}
            {manualGroups.map(renderGroupStamp)}

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

            {/* Multi-Select Marquee (Photoshop-style blue rubber band) */}
            {marqueeRect && traceTool === 'multiselect' && (
              <rect
                x={Math.min(marqueeRect.x1, marqueeRect.x2)}
                y={Math.min(marqueeRect.y1, marqueeRect.y2)}
                width={Math.abs(marqueeRect.x2 - marqueeRect.x1)}
                height={Math.abs(marqueeRect.y2 - marqueeRect.y1)}
                fill="#3B82F6"
                fillOpacity="0.12"
                stroke="#2563EB"
                strokeWidth="3"
                strokeDasharray="8 5"
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
                  Potong ({splitPreview.x}, {splitPreview.y})
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
            maxHeight: '70vh',
            overflowY: 'auto',
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

          {/* Line style: solid or dashed */}
          <div className="space-y-1">
            <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
              Gaya Garis
            </span>
            <div className="flex items-center gap-1.5">
              {([{ style: 'solid', label: 'Solid' }, { style: 'dashed', label: 'Putus-putus' }] as const).map((item) => {
                const active = singleSelectedIdx !== null && (
                  singleSelectedRun?.line_style === item.style ||
                  (item.style === 'solid' && !singleSelectedRun?.line_style)
                );
                return (
                  <button
                    key={item.style}
                    onClick={() => handleApplyLineStyle(item.style)}
                    className={`px-2.5 py-1 rounded-md border text-[11px] font-medium transition ${active ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}
                    title={`Ubah gaya garis menjadi ${item.label.toLowerCase()}`}
                  >
                    <svg aria-hidden="true" width="30" height="8" viewBox="0 0 30 8" className="inline-block mr-1.5 align-middle">
                      <line x1="1" y1="4" x2="29" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray={item.style === 'dashed' ? '4 3' : undefined} />
                    </svg>
                    {item.label}
                  </button>
                );
              })}
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
                      onKeyDown={(e) => {
                        // Contain every keystroke inside the field: Backspace/Delete/Space/
                        // Arrow keys must edit text, never trigger canvas pipe shortcuts.
                        e.stopPropagation();
                        if (e.key === 'Enter') handleSaveTag();
                      }}
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

      {/* 4. Split Mode Guide Banner */}
      {splitMode && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-red-600/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 animate-bounce max-w-[92vw] truncate">
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
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-blue-700/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 max-w-[92vw] truncate">
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

      {traceTool === 'rescan' && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-cyan-700/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 max-w-[92vw] truncate">
          <Crop className="w-3.5 h-3.5" />
          <span>Tarik kotak pada area pipa untuk scan ulang. Garis akan otomatis menyambung ke pipa yang ada.</span>
        </div>
      )}

      {traceTool === 'wand' && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-amber-600/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 max-w-[92vw] truncate">
          <Wand2 className="w-3.5 h-3.5" />
          <span>Klik tepat pada garis pipa — jejak vektor CAD terdekat langsung ditambahkan.</span>
        </div>
      )}

      {/* 6. Active Tool Dock / Pill Toolbar at Bottom-Center */}
      {/* Vertical tool rack (Photoshop-style), anchored to the RIGHT edge and vertically
          centered. Living on the right edge keeps it clear of the bottom-center
          page-controls bar (Pipa ON/OFF + opacity) at every viewport width, so no
          overlap logic / band-splitting is needed anymore. */}
      <div className="absolute right-3 top-1/2 -translate-y-1/2 z-50 flex flex-col items-stretch gap-1 rounded-2xl bg-white/95 backdrop-blur-md p-1.5 shadow-2xl border border-slate-200">
        <button
          onClick={() => setShowDetectedLines((shown) => !shown)}
          aria-pressed={showDetectedLines}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            showDetectedLines ? 'bg-slate-100 text-slate-800' : 'hover:bg-slate-100 text-slate-500'
          }`}
          title={showDetectedLines ? 'Sembunyikan garis hasil auto-trace yang belum ditandai' : 'Tampilkan garis hasil auto-trace yang belum ditandai'}
        >
          {showDetectedLines ? <Eye className="w-4 h-4 shrink-0" /> : <EyeOff className="w-4 h-4 shrink-0" />}
          <span className="hidden xl:inline">Show Detected Lines</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('pan');
            setManualPoints([]);
            setRoiRect(null);
            setMarqueeRect(null);
            marqueeStartRef.current = null;
          }}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            traceTool === 'pan'
              ? 'bg-indigo-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Pan & Select Tool: Geser kanvas atau klik garis pipa untuk edit"
        >
          <Hand className="w-4 h-4 shrink-0" />
          <span className="hidden xl:inline">Pan &amp; Select</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('wand');
            setManualPoints([]);
            setRoiRect(null);
            setMarqueeRect(null);
            marqueeStartRef.current = null;
          }}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            traceTool === 'wand'
              ? 'bg-amber-500 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Magic Wand: klik jalur auto-trace untuk menandainya; klik lagi untuk mengedit"
        >
          <Wand2 className="w-4 h-4 shrink-0" />
          <span className="hidden xl:inline">Magic Wand</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('multiselect');
            setManualPoints([]);
            setRoiRect(null);
          }}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            traceTool === 'multiselect'
              ? 'bg-violet-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Multi-Select: Tarik kotak biru untuk memilih banyak garis sekaligus, lalu hapus/ganti warna"
        >
          <BoxSelect className="w-4 h-4 shrink-0" />
          <span className="hidden xl:inline">Multi-Select</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('rescan');
            setManualPoints([]);
            setMarqueeRect(null);
            marqueeStartRef.current = null;
          }}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            traceTool === 'rescan'
              ? 'bg-cyan-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Box Trace (ROI): Tarik kotak untuk deteksi ulang area tertentu"
        >
          <Crop className="w-4 h-4 shrink-0" />
          <span className="hidden xl:inline">Box Trace</span>
        </button>

        <button
          onClick={() => {
            onSetTraceTool('pen');
            setRoiRect(null);
            setMarqueeRect(null);
            marqueeStartRef.current = null;
          }}
          className={`px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-2 transition ${
            traceTool === 'pen'
              ? 'bg-blue-600 text-white shadow-md'
              : 'hover:bg-slate-100 text-slate-700'
          }`}
          title="Manual Pen: Gambar garis pipa baru secara manual"
        >
          <Pencil className="w-4 h-4 shrink-0" />
          <span className="hidden xl:inline">Manual Pen</span>
        </button>

        {traceTool === 'pen' && manualPoints.length >= 2 && (
          <button
            disabled={toolBusy}
            onClick={finishManual}
            className="px-2.5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold transition flex items-center space-x-2 shadow"
          >
            <Check className="w-4 h-4 shrink-0" />
            <span className="hidden xl:inline">Selesai (Enter)</span>
          </button>
        )}

        {toolBusy && (
          <span className="px-1 text-[10px] font-medium text-indigo-600 animate-pulse text-center">
            Memproses...
          </span>
        )}
      </div>
    </>
  );
}
