'use client';

import { useState, useEffect, useRef } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowLeft,
  Play,
  Download,
  CheckCircle2,
  AlertTriangle,
  Layers,
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Search,
  Filter,
} from 'lucide-react';
import {
  ProjectResponse,
  SheetResponse,
  DigitizationResult,
  CorrosionSystem,
  ValidationReport,
} from '@/types/schema';
import {
  fetchProject,
  fetchResult,
  fetchSystems,
  fetchValidation,
  triggerDetection,
  getRawImageUrl,
  getExportUrl,
} from '@/lib/api';

type ViewMode = 'digitize' | 'system' | 'circuit' | 'report';

export default function ProjectWorkspace() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const projectId = params.id as string;
  const sheetIdParam = searchParams.get('sheetId');

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [activeSheet, setActiveSheet] = useState<SheetResponse | null>(null);
  const [mode, setMode] = useState<ViewMode>('digitize');

  const [result, setResult] = useState<DigitizationResult | null>(null);
  const [systems, setSystems] = useState<CorrosionSystem[]>([]);
  const [validation, setValidation] = useState<ValidationReport | null>(null);

  const [detecting, setDetecting] = useState(false);
  const [progressMsg, setProgressMsg] = useState('');
  const [progressPct, setProgressPct] = useState(0);

  const [selectedPidIdx, setSelectedPidIdx] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterFluid, setFilterFluid] = useState<string>('all');

  const canvasRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);

  // Load project & sheets
  useEffect(() => {
    if (!projectId) return;
    fetchProject(projectId).then((p) => {
      setProject(p);
      if (p.sheets && p.sheets.length > 0) {
        const target = sheetIdParam
          ? p.sheets.find((s) => s.id === sheetIdParam) || p.sheets[0]
          : p.sheets[0];
        setActiveSheet(target);
      }
    });
  }, [projectId, sheetIdParam]);

  // Load detection & grouping data when active sheet changes
  useEffect(() => {
    if (!projectId || !activeSheet) return;
    if (activeSheet.status === 'detected') {
      fetchResult(projectId, activeSheet.id)
        .then(setResult)
        .catch(console.error);
      fetchSystems(projectId, activeSheet.id)
        .then(setSystems)
        .catch(console.error);
      fetchValidation(projectId, activeSheet.id)
        .then(setValidation)
        .catch(console.error);
    } else {
      setResult(null);
      setSystems([]);
      setValidation(null);
    }
  }, [projectId, activeSheet]);

  // Initialize OpenSeadragon Canvas
  useEffect(() => {
    if (!canvasRef.current || !activeSheet) return;

    let viewer: any = null;
    let isCancelled = false;

    import('openseadragon').then((OpenSeadragon) => {
      if (isCancelled || !canvasRef.current) return;

      if (viewerRef.current) {
        viewerRef.current.destroy();
      }

      viewer = OpenSeadragon.default({
        element: canvasRef.current,
        prefixUrl: 'https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.1/images/',
        tileSources: {
          type: 'image',
          url: getRawImageUrl(projectId, activeSheet.id),
        },
        showNavigationControl: false,
        animationTime: 0.3,
        blendTime: 0.1,
        constrainDuringPan: true,
        maxZoomPixelRatio: 3,
        minZoomImageRatio: 0.8,
        gestureSettingsMouse: {
          clickToZoom: false,
          dblClickToZoom: true,
        },
      });

      viewerRef.current = viewer;
    });

    return () => {
      isCancelled = true;
      if (viewerRef.current) {
        viewerRef.current.destroy();
        viewerRef.current = null;
      }
    };
  }, [projectId, activeSheet]);

  // Trigger Detection Pipeline
  const handleRunDetection = async () => {
    if (!projectId || !activeSheet) return;
    try {
      setDetecting(true);
      setProgressPct(0);
      setProgressMsg('Initializing P&ID pipeline...');

      const job = await triggerDetection(projectId, activeSheet.id);

      // WebSocket connection for live progress
      const wsUrl = (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000') + `/ws/progress/${job.job_id}`;
      const ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.pct !== undefined) setProgressPct(data.pct);
          if (data.message) setProgressMsg(data.message);
          if (data.step === 'completed') {
            ws.close();
            setDetecting(false);
            // Reload sheet data
            fetchResult(projectId, activeSheet.id).then(setResult);
            fetchSystems(projectId, activeSheet.id).then(setSystems);
            fetchValidation(projectId, activeSheet.id).then(setValidation);
          } else if (data.step === 'failed') {
            ws.close();
            setDetecting(false);
            alert('Detection error: ' + data.message);
          }
        } catch (err) {}
      };

      ws.onerror = () => {
        // Fallback polling if WS is not reachable
        const interval = setInterval(async () => {
          const res = await fetchProject(projectId);
          const s = res.sheets.find((sh) => sh.id === activeSheet.id);
          if (s && s.status === 'detected') {
            clearInterval(interval);
            setDetecting(false);
            setActiveSheet(s);
          }
        }, 3000);
      };
    } catch (err) {
      setDetecting(false);
      alert('Failed to trigger detection: ' + err);
    }
  };

  const handleZoom = (delta: number) => {
    if (!viewerRef.current) return;
    const current = viewerRef.current.viewport.getZoom();
    viewerRef.current.viewport.zoomTo(current * (delta > 0 ? 1.3 : 0.7));
  };

  const handleResetZoom = () => {
    if (!viewerRef.current) return;
    viewerRef.current.viewport.goHome();
  };

  return (
    <div className="h-full flex flex-col bg-slate-100 text-slate-900 overflow-hidden select-none">
      {/* Top Navigation Bar */}
      <header className="bg-white border-b border-slate-200 px-6 py-2.5 flex items-center justify-between shadow-sm z-20">
        <div className="flex items-center space-x-4">
          <Link
            href="/"
            className="p-1.5 hover:bg-slate-100 text-slate-600 rounded-lg transition"
            title="Back to Projects"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="font-bold text-base text-slate-900 leading-tight">
                {project?.name || 'Loading...'}
              </h1>
              {activeSheet && (
                <span className="text-xs bg-indigo-50 text-indigo-700 font-semibold px-2 py-0.5 rounded">
                  {activeSheet.filename}
                </span>
              )}
            </div>
            <p className="text-[11px] text-slate-500 font-medium">
              API RP 970 · Corrosion Control Document (CCD)
            </p>
          </div>
        </div>

        {/* View Mode Switcher */}
        <div className="flex bg-slate-100 p-1 rounded-xl border border-slate-200">
          <button
            onClick={() => setMode('digitize')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition ${
              mode === 'digitize'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Digitization
          </button>
          <button
            onClick={() => setMode('system')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition ${
              mode === 'system'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Corrosion System
          </button>
          <button
            onClick={() => setMode('circuit')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition ${
              mode === 'circuit'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Corrosion Circuit
          </button>
          <button
            onClick={() => setMode('report')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition ${
              mode === 'report'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Validation & Report
          </button>
        </div>

        {/* Action Controls */}
        <div className="flex items-center space-x-2">
          {activeSheet && (
            <button
              onClick={handleRunDetection}
              disabled={detecting}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow transition ${
                detecting
                  ? 'bg-slate-300 text-slate-500 cursor-not-allowed'
                  : 'bg-indigo-600 hover:bg-indigo-700 text-white'
              }`}
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{detecting ? 'Detecting...' : 'Detect P&ID'}</span>
            </button>
          )}

          {/* Export Dropdown Menu */}
          {result && (
            <div className="relative group">
              <button className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition">
                <Download className="w-3.5 h-3.5" />
                <span>Export</span>
              </button>
              <div className="absolute right-0 top-full mt-1 w-44 bg-white border border-slate-200 rounded-xl shadow-xl py-1 hidden group-hover:block z-50">
                <a
                  href={getExportUrl(projectId, activeSheet!.id, 'xlsx')}
                  download
                  className="block px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50 hover:text-indigo-600 font-medium"
                >
                  Excel Line Register (.xlsx)
                </a>
                <a
                  href={getExportUrl(projectId, activeSheet!.id, 'docx')}
                  download
                  className="block px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50 hover:text-indigo-600 font-medium"
                >
                  Word Asset Register (.docx)
                </a>
                <a
                  href={getExportUrl(projectId, activeSheet!.id, 'pdf', mode === 'circuit' ? 'circuit' : 'system')}
                  download
                  className="block px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50 hover:text-indigo-600 font-medium"
                >
                  Acrobat Vector PDF (.pdf)
                </a>
                <a
                  href={getExportUrl(projectId, activeSheet!.id, 'png', mode === 'circuit' ? 'circuit' : 'system')}
                  download
                  className="block px-3 py-1.5 text-xs text-slate-700 hover:bg-indigo-50 hover:text-indigo-600 font-medium"
                >
                  Marked PNG Image (.png)
                </a>
              </div>
            </div>
          )}
        </div>
      </header>

      {/* Progress Bar (during detection) */}
      {detecting && (
        <div className="bg-indigo-50 border-b border-indigo-100 px-6 py-2 flex items-center justify-between text-xs text-indigo-900 font-medium">
          <div className="flex items-center space-x-2">
            <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-600" />
            <span>{progressMsg || 'Processing P&ID...'}</span>
          </div>
          <div className="flex items-center space-x-3">
            <div className="w-36 h-2 bg-indigo-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-600 transition-all duration-300"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <span className="font-bold text-indigo-700">{progressPct}%</span>
          </div>
        </div>
      )}

      {/* Workspace Body: Canvas (Left) + Data Panel (Right) */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: OpenSeadragon Canvas Area */}
        <div className="flex-1 relative bg-slate-900 overflow-hidden">
          {/* Canvas Container */}
          <div ref={canvasRef} className="w-full h-full" />

          {/* Floating Canvas Controls */}
          <div className="absolute bottom-6 left-6 flex bg-white/90 backdrop-blur border border-slate-300 rounded-xl shadow-lg p-1 space-x-1 z-10">
            <button
              onClick={() => handleZoom(1)}
              className="p-2 hover:bg-slate-100 rounded-lg text-slate-700 transition"
              title="Zoom In"
            >
              <ZoomIn className="w-4 h-4" />
            </button>
            <button
              onClick={() => handleZoom(-1)}
              className="p-2 hover:bg-slate-100 rounded-lg text-slate-700 transition"
              title="Zoom Out"
            >
              <ZoomOut className="w-4 h-4" />
            </button>
            <button
              onClick={handleResetZoom}
              className="p-2 hover:bg-slate-100 rounded-lg text-slate-700 transition"
              title="Reset View"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>

          {/* Canvas Overlay Legend */}
          {mode === 'system' && systems.length > 0 && (
            <div className="absolute top-4 left-4 bg-white/95 backdrop-blur border border-slate-200 rounded-xl shadow-xl p-3 max-w-xs max-h-80 overflow-y-auto z-10 text-xs">
              <h4 className="font-bold text-slate-900 uppercase tracking-wider text-[10px] mb-2">
                Corrosion Systems (By Fluid)
              </h4>
              <div className="space-y-1.5">
                {systems.map((s) => (
                  <div key={s.fluid} className="flex items-center space-x-2">
                    <span
                      className="w-3.5 h-3.5 rounded shadow-sm shrink-0"
                      style={{ backgroundColor: `rgb(${s.color.join(',')})` }}
                    />
                    <span className="font-bold text-slate-800">{s.fluid}</span>
                    <span className="text-slate-500 text-[11px]">({s.n_pipes} pipes)</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {mode === 'circuit' && systems.length > 0 && (
            <div className="absolute top-4 left-4 bg-white/95 backdrop-blur border border-slate-200 rounded-xl shadow-xl p-3 max-w-xs max-h-80 overflow-y-auto z-10 text-xs">
              <h4 className="font-bold text-slate-900 uppercase tracking-wider text-[10px] mb-2">
                Corrosion Circuits (API RP 970)
              </h4>
              <div className="space-y-1.5">
                {systems.flatMap((s) =>
                  s.circuits.map((c) => (
                    <div key={c.code} className="flex items-center space-x-2">
                      <span
                        className="w-3.5 h-3.5 rounded shadow-sm shrink-0"
                        style={{ backgroundColor: `rgb(${c.color.join(',')})` }}
                      />
                      <span className="font-bold text-slate-800">{c.code}</span>
                      <span className="text-slate-600">{s.fluid}-{c.material || '—'}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right: Inspection & Data Tables Panel */}
        <div className="w-96 bg-white border-l border-slate-200 flex flex-col shadow-xl z-10">
          {/* Panel Header */}
          <div className="p-4 border-b border-slate-200 flex items-center justify-between">
            <h3 className="font-bold text-sm text-slate-900 uppercase tracking-wider">
              {mode === 'digitize' && 'Piping Line Register'}
              {mode === 'system' && 'Corrosion Systems'}
              {mode === 'circuit' && 'Circuit Breakdown'}
              {mode === 'report' && 'Engineering Audit'}
            </h3>
            {result && (
              <span className="text-xs bg-slate-100 text-slate-600 font-semibold px-2 py-0.5 rounded">
                {mode === 'digitize' && `${result.piping_ids.length} lines`}
                {mode === 'system' && `${systems.length} systems`}
                {mode === 'circuit' &&
                  `${systems.reduce((acc, s) => acc + s.circuits.length, 0)} circuits`}
                {mode === 'report' &&
                  (validation?.passed ? 'PASS' : 'REVIEW')}
              </span>
            )}
          </div>

          {/* Search bar for tables */}
          {mode === 'digitize' && (
            <div className="p-3 border-b border-slate-100 bg-slate-50">
              <div className="relative">
                <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5" />
                <input
                  type="text"
                  placeholder="Filter by line, fluid, or class..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 bg-white border border-slate-200 rounded-lg text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            </div>
          )}

          {/* Panel Content */}
          <div className="flex-1 overflow-y-auto">
            {!result ? (
              <div className="p-8 text-center text-slate-400 text-xs">
                Click &quot;Detect P&amp;ID&quot; to digitize components and generate corrosion circuits.
              </div>
            ) : mode === 'digitize' ? (
              <div className="divide-y divide-slate-100">
                {result.piping_ids
                  .filter((p) => {
                    if (!searchQuery) return true;
                    const q = searchQuery.toLowerCase();
                    return (
                      p.pid.toLowerCase().includes(q) ||
                      p.fluid.toLowerCase().includes(q) ||
                      p.pclass.toLowerCase().includes(q)
                    );
                  })
                  .map((p, idx) => (
                    <div
                      key={p.pid + idx}
                      onClick={() => setSelectedPidIdx(idx)}
                      className={`p-3 cursor-pointer transition text-xs ${
                        selectedPidIdx === idx
                          ? 'bg-indigo-50 border-l-4 border-indigo-600'
                          : 'hover:bg-slate-50'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-900">{p.pid}</span>
                        <span
                          className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                            p.state === 'attached'
                              ? 'bg-emerald-100 text-emerald-700'
                              : p.state === 'leader'
                              ? 'bg-blue-100 text-blue-700'
                              : 'bg-amber-100 text-amber-700'
                          }`}
                        >
                          {p.state}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center space-x-3 text-slate-500 text-[11px]">
                        <span>Fluid: <b className="text-slate-700">{p.fluid || '—'}</b></span>
                        <span>Class: <b className="text-slate-700">{p.pclass || '—'}</b></span>
                        <span>Size: <b className="text-slate-700">{p.size || '—'}</b></span>
                      </div>
                    </div>
                  ))}
              </div>
            ) : mode === 'system' ? (
              <div className="p-4 space-y-3">
                {systems.map((s) => (
                  <div key={s.fluid} className="border border-slate-200 rounded-xl p-3 bg-slate-50">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span
                          className="w-3.5 h-3.5 rounded"
                          style={{ backgroundColor: `rgb(${s.color.join(',')})` }}
                        />
                        <span className="font-bold text-slate-900 text-sm">
                          System #{String(s.index).padStart(2, '0')}
                        </span>
                      </div>
                      <span className="font-bold text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded text-xs">
                        {s.fluid}
                      </span>
                    </div>
                    <div className="mt-2 text-xs text-slate-600 flex justify-between">
                      <span>Piping Lines: <b>{s.pid_idxs.length}</b></span>
                      <span>Traced Pipe Runs: <b>{s.n_pipes}</b></span>
                      <span>Circuits: <b>{s.circuits.length}</b></span>
                    </div>
                  </div>
                ))}
              </div>
            ) : mode === 'circuit' ? (
              <div className="p-4 space-y-3">
                {systems.flatMap((s) =>
                  s.circuits.map((c) => (
                    <div key={c.code} className="border border-slate-200 rounded-xl p-3 bg-slate-50">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <span
                            className="w-3.5 h-3.5 rounded"
                            style={{ backgroundColor: `rgb(${c.color.join(',')})` }}
                          />
                          <span className="font-bold text-slate-900 text-sm">Circuit {c.code}</span>
                        </div>
                        <span className="font-semibold text-slate-700 bg-white border border-slate-200 px-2 py-0.5 rounded text-xs">
                          {s.fluid} - {c.material || 'General'}
                        </span>
                      </div>
                      <div className="mt-2 text-xs text-slate-600">
                        <div>Piping Classes: <b>{c.classes.join(', ') || '—'}</b></div>
                        <div>Associated Lines: <b>{c.pid_idxs.length} line(s)</b></div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            ) : (
              <div className="p-4 space-y-4">
                {validation && (
                  <>
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-center">
                      <div className="text-2xl font-black text-indigo-600">
                        {validation.score} / 100
                      </div>
                      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mt-0.5">
                        Quality Confidence Score
                      </div>
                    </div>

                    <div className="space-y-2">
                      <h4 className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                        Integrity Checks
                      </h4>
                      {validation.checks.map((c) => (
                        <div
                          key={c.id}
                          className="flex items-center justify-between text-xs p-2 rounded-lg bg-slate-50 border border-slate-100"
                        >
                          <div className="flex items-center space-x-2 truncate">
                            {c.passed ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                            ) : (
                              <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
                            )}
                            <span className="truncate font-medium text-slate-800">{c.name}</span>
                          </div>
                          <span className="font-bold text-slate-700 shrink-0">{c.value}%</span>
                        </div>
                      ))}
                    </div>

                    {validation.flags && validation.flags.length > 0 && (
                      <div className="space-y-2 pt-2">
                        <h4 className="text-xs font-bold text-amber-700 uppercase tracking-wider">
                          Items Requiring Confirmation ({validation.flags.length})
                        </h4>
                        {validation.flags.map((f, i) => (
                          <div
                            key={i}
                            className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-900"
                          >
                            <span className="font-bold uppercase text-[10px] bg-amber-200 px-1 rounded mr-1.5">
                              {f.kind}
                            </span>
                            {f.msg_en || f.msg}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
