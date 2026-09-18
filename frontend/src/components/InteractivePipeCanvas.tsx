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
} from 'lucide-react';
import { PipeRun, PipingID } from '@/types/schema';

// 5 Quick Colors requested: Biru #2563EB, Hijau #10B981, Merah #EF4444, Kuning #F59E0B, Ungu #8B5CF6
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
  onDeleteRuns,
  onUpdateRunLabel,
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
  const [deleting, setDeleting] = useState(false);
  const [savingTag, setSavingTag] = useState(false);
  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number; imgX: number; imgY: number } | null>(null);
  const [tagInput, setTagInput] = useState<string>('');

  const svgRef = useRef<SVGSVGElement | null>(null);

  // Mount OpenSeadragon overlay container that syncs with pan & zoom
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

  // Line click handler with Shift+Click multi-select and dynamic popover placement
  const handleLineClick = (idx: number, e: React.MouseEvent) => {
    e.stopPropagation();

    // If split mode is active and this is the selected line, execute split
    if (splitMode && selectedRunIndices.has(idx) && splitPreview) {
      handleExecuteSplit(idx, splitPreview.x, splitPreview.y);
      return;
    }

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
  const associatedPid = singleSelectedIdx !== null
    ? pipingIds.find((p) => p.run_idx === singleSelectedIdx)?.pid
    : null;

  // Sync tag input with selected run
  useEffect(() => {
    if (singleSelectedRun) {
      setTagInput(singleSelectedRun.label || associatedPid || '');
    } else {
      setTagInput('');
    }
  }, [singleSelectedRun, associatedPid]);

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
              display: showOverlay ? 'block' : 'none',
              opacity: opacity,
              transition: 'opacity 0.2s ease',
            }}
            onMouseMove={handleSvgMouseMove}
            onClick={() => {
              if (selectedRunIndices.size > 0 && !splitMode) {
                onSelectRunIndices(new Set());
                setPopoverPos(null);
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
          style={{
            left: popoverPos?.x ?? 24,
            top: popoverPos?.y ?? 24,
            minWidth: 280,
            maxWidth: 340,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Popover Header: Info + Close */}
          <div className="flex items-center justify-between border-b border-slate-100 pb-2">
            <div className="flex items-center space-x-2">
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
              className="p-1 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition"
              title="Tutup (Esc)"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Quick Color Palette: 5 Quick Colors + Hex Picker */}
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

      {/* 3. Split Mode Guide Banner (when user is in cut mode) */}
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
    </>
  );
}
