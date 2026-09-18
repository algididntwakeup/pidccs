'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  Scissors,
  Check,
  X,
  Palette,
  Eye,
  EyeOff,
  Undo2,
  Redo2,
  Sliders,
  Sparkles,
  Info,
} from 'lucide-react';
import { PipeRun, PipingID } from '@/types/schema';

// Preset colors recommended for engineering P&ID marking
export const COLOR_PALETTE = [
  { name: 'Neutral Blue (Default)', hex: '#2563EB' },
  { name: 'Corrosion Red', hex: '#DC2626' },
  { name: 'Process Green', hex: '#16A34A' },
  { name: 'Hazard Amber', hex: '#CA8A04' },
  { name: 'Spec Break Purple', hex: '#9333EA' },
  { name: 'High Temp Orange', hex: '#EA580C' },
  { name: 'Utility Teal', hex: '#0D9488' },
  { name: 'Low Temp Cyan', hex: '#0284C7' },
  { name: 'Special Alloy Rose', hex: '#E11D48' },
  { name: 'Neutral Slate', hex: '#475569' },
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
  splitMode: boolean;
  onSetSplitMode: (active: boolean) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
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
  splitMode,
  onSetSplitMode,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
}: InteractivePipeCanvasProps) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [hoveredRunIdx, setHoveredRunIdx] = useState<number | null>(null);
  const [splitPreview, setSplitPreview] = useState<{ x: number; y: number } | null>(null);
  const [customColor, setCustomColor] = useState('#2563EB');
  const [splitting, setSplitting] = useState(false);

  const svgRef = useRef<SVGSVGElement | null>(null);

  // Mount an OpenSeadragon overlay container that syncs with pan & zoom
  useEffect(() => {
    if (!viewer || !osdModule || !width || !height) return;

    const overlayEl = document.createElement('div');
    overlayEl.id = 'pid-interactive-svg-overlay-container';
    overlayEl.style.width = '100%';
    overlayEl.style.height = '100%';
    overlayEl.style.position = 'absolute';
    overlayEl.style.top = '0';
    overlayEl.style.left = '0';
    overlayEl.style.pointerEvents = 'none';

    const aspectRatio = height / width;
    const rect = new osdModule.Rect(0, 0, 1.0, aspectRatio);

    viewer.addOverlay({
      element: overlayEl,
      location: rect,
      checkResize: false,
    });

    setContainer(overlayEl);

    return () => {
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

  // Coordinate conversion: Browser mouse -> SVG Drawing Pixel Coordinate
  const getImageCoordinates = useCallback((e: React.MouseEvent): { x: number; y: number } | null => {
    if (!svgRef.current) return null;
    const svg = svgRef.current;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const transformed = pt.matrixTransform(ctm.inverse());
    return { x: transformed.x, y: transformed.y };
  }, []);

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

  // Line click handler with Shift+Click multi-select support
  const handleLineClick = (idx: number, e: React.MouseEvent) => {
    e.stopPropagation();

    // If split mode is active and this is the selected line, execute split
    if (splitMode && selectedRunIndices.has(idx) && splitPreview) {
      handleExecuteSplit(idx, splitPreview.x, splitPreview.y);
      return;
    }

    if (e.shiftKey) {
      // Multi-select toggle
      const next = new Set(selectedRunIndices);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      onSelectRunIndices(next);
    } else {
      // Single select
      onSelectRunIndices(new Set([idx]));
    }
  };

  // Mouse move over SVG to update split preview
  const handleSvgMouseMove = (e: React.MouseEvent) => {
    if (!splitMode || selectedRunIndices.size !== 1) {
      if (splitPreview) setSplitPreview(null);
      return;
    }

    const selectedIdx = Array.from(selectedRunIndices)[0];
    const selectedRun = runs[selectedIdx];
    if (!selectedRun) return;

    const coords = getImageCoordinates(e);
    if (!coords) return;

    const proj = findClosestPointOnRun(selectedRun, coords.x, coords.y);
    // If within reasonable proximity of the pipe line (~120px)
    if (proj.dist <= 120) {
      setSplitPreview({ x: Math.round(proj.x), y: Math.round(proj.y) });
    } else {
      setSplitPreview(null);
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
    } finally {
      setSplitting(false);
    }
  };

  // Recolor action for currently selected runs
  const handleApplyColor = (colorHex: string) => {
    if (selectedRunIndices.size === 0) return;
    onRecolorRuns(Array.from(selectedRunIndices), colorHex);
  };

  const computeRunLength = (run: PipeRun | null | undefined) => {
    if (!run || !run.points || run.points.length < 2) return 0;
    let len = 0;
    for (let i = 0; i < run.points.length - 1; i++) {
      len += Math.hypot(run.points[i + 1][0] - run.points[i][0], run.points[i + 1][1] - run.points[i][1]);
    }
    return len;
  };

  // Get metadata for single selected run
  const singleSelectedIdx = selectedRunIndices.size === 1 ? Array.from(selectedRunIndices)[0] : null;
  const singleSelectedRun = singleSelectedIdx !== null ? runs[singleSelectedIdx] : null;
  const associatedPid = singleSelectedIdx !== null
    ? pipingIds.find((p) => p.run_idx === singleSelectedIdx)?.pid
    : null;

  return (
    <>
      {/* 1. Interactive SVG Overlay inside OpenSeadragon Canvas */}
      {container &&
        createPortal(
          <svg
            ref={svgRef}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            style={{
              width: '100%',
              height: '100%',
              position: 'absolute',
              top: 0,
              left: 0,
              pointerEvents: 'none',
              overflow: 'visible',
              opacity: showOverlay ? opacity : 0,
              transition: 'opacity 0.2s ease',
            }}
            onMouseMove={handleSvgMouseMove}
            onClick={() => {
              // Click outside any line deselects
              if (selectedRunIndices.size > 0 && !splitMode) {
                onSelectRunIndices(new Set());
              }
            }}
          >
            <defs>
              {/* Hover Glow */}
              <filter id="hover-glow" x="-30%" y="-30%" width="160%" height="160%">
                <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="#38BDF8" floodOpacity="0.9" />
              </filter>
              {/* Selection Halo */}
              <filter id="select-halo" x="-40%" y="-40%" width="180%" height="180%">
                <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor="#F59E0B" floodOpacity="0.95" />
              </filter>
            </defs>

            {/* Polyline Pipe Runs */}
            {runs.map((run, idx) => {
              if (!run.points || run.points.length < 2) return null;
              const isSelected = selectedRunIndices.has(idx);
              const isHovered = hoveredRunIdx === idx;
              const strokeColor = run.color || '#2563EB';
              const ptsStr = run.points.map((p) => `${p[0]},${p[1]}`).join(' ');

              return (
                <g key={`run-${idx}`} className="group">
                  {/* Invisible wide stroke for easy clicking & hovering */}
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
                    style={{
                      pointerEvents: 'none',
                      transition: 'stroke 0.15s ease, stroke-width 0.15s ease',
                    }}
                  />

                  {/* Endpoint Dots for Selected Lines */}
                  {isSelected && (
                    <>
                      <circle
                        cx={run.points[0][0]}
                        cy={run.points[0][1]}
                        r={6}
                        fill="#F59E0B"
                        stroke="#FFFFFF"
                        strokeWidth={2}
                        style={{ pointerEvents: 'none' }}
                      />
                      <circle
                        cx={run.points[run.points.length - 1][0]}
                        cy={run.points[run.points.length - 1][1]}
                        r={6}
                        fill="#F59E0B"
                        stroke="#FFFFFF"
                        strokeWidth={2}
                        style={{ pointerEvents: 'none' }}
                      />
                    </>
                  )}
                </g>
              );
            })}

            {/* Split Mode Interactive Cut Point Marker */}
            {splitMode && splitPreview && (
              <g pointerEvents="none">
                {/* Crosshair indicator */}
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
                {/* Coordinates Label */}
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

      {/* 2. Floating Toolbar for Line Recoloring & Splitting */}
      {selectedRunIndices.size > 0 && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 bg-white/95 backdrop-blur border border-slate-200 rounded-2xl shadow-2xl p-2 px-4 flex items-center space-x-3 text-xs animate-in fade-in slide-in-from-top-3 duration-200">
          {/* Selection Info Pill */}
          <div className="flex items-center space-x-2 shrink-0">
            {singleSelectedIdx !== null ? (
              <div className="flex items-center space-x-1.5 px-2.5 py-1 bg-indigo-50 border border-indigo-200 rounded-lg font-semibold text-indigo-900">
                <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
                <span>Pipa #{singleSelectedIdx}</span>
                {associatedPid && (
                  <span className="text-indigo-600 font-mono text-[11px] font-bold">
                    [{associatedPid}]
                  </span>
                )}
                <span className="text-[10px] text-slate-500">
                  ({Math.round(computeRunLength(singleSelectedRun))} px)
                </span>
              </div>
            ) : (
              <div className="flex items-center space-x-1.5 px-2.5 py-1 bg-amber-50 border border-amber-200 rounded-lg font-semibold text-amber-900">
                <span>{selectedRunIndices.size} Pipa Terpilih</span>
                <span className="text-[10px] text-amber-700">(Shift+Click)</span>
              </div>
            )}
          </div>

          <div className="w-[1px] h-6 bg-slate-200 shrink-0" />

          {/* Quick Color Palette Swatches */}
          <div className="flex items-center space-x-1.5 shrink-0">
            {COLOR_PALETTE.map((c) => (
              <button
                key={c.hex}
                onClick={() => handleApplyColor(c.hex)}
                className="w-5 h-5 rounded-full border border-black/10 hover:scale-125 transition-transform shadow-sm focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-indigo-500"
                style={{ backgroundColor: c.hex }}
                title={`Ubah ke ${c.name} (${c.hex})`}
              />
            ))}

            {/* Custom Hex Color Picker */}
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

          <div className="w-[1px] h-6 bg-slate-200 shrink-0" />

          {/* Split Line Button (Active only when 1 line selected) */}
          {singleSelectedIdx !== null && (
            <button
              onClick={() => onSetSplitMode(!splitMode)}
              className={`px-3 py-1.5 rounded-lg font-semibold flex items-center space-x-1.5 transition ${
                splitMode
                  ? 'bg-red-600 text-white shadow-md animate-pulse'
                  : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
              }`}
              title="Potong garis ini menjadi 2 pipa terpisah"
            >
              <Scissors className="w-3.5 h-3.5" />
              <span>{splitMode ? 'Batal Split' : 'Split Line'}</span>
            </button>
          )}

          {/* Close / Deselect Button */}
          <button
            onClick={() => {
              onSelectRunIndices(new Set());
              onSetSplitMode(false);
            }}
            className="p-1 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition"
            title="Tutup seleksi (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 3. Split Mode Guide Banner (when user is in cut mode) */}
      {splitMode && (
        <div className="absolute top-20 left-1/2 -translate-x-1/2 z-40 bg-red-600/95 backdrop-blur text-white px-4 py-1.5 rounded-full shadow-xl text-xs font-semibold flex items-center space-x-2 animate-bounce">
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
    </>
  );
}
