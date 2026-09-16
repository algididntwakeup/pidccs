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
  Upload,
  ExternalLink,
  Network,
  FileSpreadsheet,
  ShieldCheck,
  Edit3,
  X,
  Check,
  ArrowRight,
} from 'lucide-react';
import {
  ProjectResponse,
  SheetResponse,
  DigitizationResult,
  CorrosionSystem,
  ValidationReport,
  PipingID,
  OffPageConnector,
  ProjectTopologyResponse,
} from '@/types/schema';
import {
  fetchProject,
  fetchResult,
  fetchSystems,
  fetchValidation,
  triggerDetection,
  getRawImageUrl,
  getExportUrl,
  uploadLineList,
  fetchProjectTopology,
  patchResult,
} from '@/lib/api';

type ViewMode = 'digitize' | 'system' | 'circuit' | 'report' | 'topology';

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
  const [topology, setTopology] = useState<ProjectTopologyResponse | null>(null);

  const [detecting, setDetecting] = useState(false);
  const [progressMsg, setProgressMsg] = useState('');
  const [progressPct, setProgressPct] = useState(0);

  const [selectedPidIdx, setSelectedPidIdx] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterFluid, setFilterFluid] = useState<string>('all');

  // Line list modal state
  const [showLineListModal, setShowLineListModal] = useState(false);
  const [uploadingLineList, setUploadingLineList] = useState(false);

  // Engineer Edit modal state
  const [editingPid, setEditingPid] = useState<PipingID | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

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

  // Load topology data when mode is topology
  useEffect(() => {
    if (!projectId) return;
    if (mode === 'topology') {
      fetchProjectTopology(projectId).then(setTopology).catch(console.error);
    }
  }, [projectId, mode]);

  const handleUploadLineListFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !projectId) return;
    try {
      setUploadingLineList(true);
      await uploadLineList(projectId, file);
      setShowLineListModal(false);
      // Refresh active sheet data
      if (activeSheet) {
        fetchResult(projectId, activeSheet.id).then(setResult);
        fetchSystems(projectId, activeSheet.id).then(setSystems);
      }
      fetchProject(projectId).then(setProject);
      alert('Line List successfully imported! Operating parameters enriched.');
    } catch (err: any) {
      alert('Failed to upload Line List: ' + err.message);
    } finally {
      setUploadingLineList(false);
    }
  };

  const handleSelectOpc = (opc: OffPageConnector) => {
    if (!project || !project.sheets) return;
    const targetRef = (opc.target_sheet_number || opc.target_drawing || '').toLowerCase();
    const found = project.sheets.find(
      (s) =>
        (s.sheet_number && s.sheet_number.toLowerCase() === targetRef) ||
        s.filename.toLowerCase().includes(targetRef)
    );
    if (found) {
      setActiveSheet(found);
      router.push(`/project/${projectId}?sheetId=${found.id}`);
    } else {
      alert(`Target drawing '${opc.target_drawing || opc.target_sheet_number}' is not currently loaded in this project.`);
    }
  };

  const handleSaveEditedPid = async () => {
    if (!editingPid || !result || !activeSheet || !projectId) return;
    try {
      setSavingEdit(true);
      const updatedPids = result.piping_ids.map((p) =>
        p.pid === editingPid.pid ? { ...p, ...editingPid, manual: true } : p
      );
      const updated = await patchResult(projectId, activeSheet.id, {
        ...result,
        piping_ids: updatedPids,
      });
      setResult(updated);
      fetchSystems(projectId, activeSheet.id).then(setSystems);
      setEditingPid(null);
    } catch (err: any) {
      alert('Failed to save manual edit: ' + err.message);
    } finally {
      setSavingEdit(false);
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
            onClick={() => setMode('topology')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition ${
              mode === 'topology'
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Multi-Sheet Topology
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
          {/* Import Line List Button */}
          <button
            onClick={() => setShowLineListModal(true)}
            className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition"
            title="Import Line List Excel / CSV"
          >
            <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-600" />
            <span>Line List</span>
          </button>

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
              {mode === 'topology' && 'Multi-Sheet Topology'}
            </h3>
            {mode === 'topology' ? (
              <span className="text-xs bg-indigo-50 text-indigo-700 font-semibold px-2 py-0.5 rounded">
                {topology?.edges.length || 0} links
              </span>
            ) : result ? (
              <span className="text-xs bg-slate-100 text-slate-600 font-semibold px-2 py-0.5 rounded">
                {mode === 'digitize' && `${result.piping_ids.length} lines`}
                {mode === 'system' && `${systems.length} systems`}
                {mode === 'circuit' &&
                  `${systems.reduce((acc, s) => acc + s.circuits.length, 0)} circuits`}
                {mode === 'report' &&
                  (validation?.passed ? 'PASS' : 'REVIEW')}
              </span>
            ) : null}
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
            {mode === 'topology' ? (
              <div className="p-4 space-y-4">
                {!topology ? (
                  <div className="p-8 text-center text-slate-400 text-xs">
                    <RefreshCw className="w-4 h-4 animate-spin mx-auto mb-2 text-indigo-600" />
                    Loading project topology...
                  </div>
                ) : (
                  <>
                    {/* Topology Overview Metrics */}
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="bg-slate-50 border border-slate-200 rounded-lg p-2">
                        <div className="text-lg font-black text-indigo-600">{topology.nodes.length}</div>
                        <div className="text-[10px] uppercase font-bold text-slate-500">Sheets</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-200 rounded-lg p-2">
                        <div className="text-lg font-black text-blue-600">{topology.edges.length}</div>
                        <div className="text-[10px] uppercase font-bold text-slate-500">OPC Links</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-200 rounded-lg p-2">
                        <div className="text-lg font-black text-emerald-600">{topology.circuits.length}</div>
                        <div className="text-[10px] uppercase font-bold text-slate-500">Circuits</div>
                      </div>
                    </div>

                    {/* Inter-Sheet Connections List */}
                    <div>
                      <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                        <Network className="w-3.5 h-3.5 text-indigo-600" />
                        Inter-Sheet Connections ({topology.edges.length})
                      </h4>
                      {topology.edges.length === 0 ? (
                        <div className="p-4 bg-slate-50 rounded-lg border border-slate-100 text-slate-400 text-center text-xs">
                          No inter-sheet connectors identified yet.
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {topology.edges.map((edge, idx) => (
                            <div key={idx} className="p-2.5 bg-slate-50 border border-slate-200 rounded-lg text-xs space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="font-bold text-slate-900 truncate">
                                  {edge.piping_id || edge.source_opc_id || 'Inter-Sheet Link'}
                                </span>
                                <span
                                  className={`text-[9px] font-bold px-1.5 py-0.5 rounded uppercase ${
                                    edge.confidence >= 0.8
                                      ? 'bg-emerald-100 text-emerald-700'
                                      : 'bg-amber-100 text-amber-700'
                                  }`}
                                >
                                  {edge.confidence >= 0.8 ? 'resolved' : 'pending'}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 text-[11px] text-slate-600">
                                <span className="truncate">Sheet: {edge.source_sheet_id.slice(0, 8)}...</span>
                                <ArrowRight className="w-3 h-3 text-slate-400 shrink-0" />
                                <span className="truncate font-semibold text-indigo-700">
                                  {edge.target_sheet_id ? `Sheet ${edge.target_sheet_id.slice(0, 8)}...` : 'Target Drawing'}
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Unified Multi-Sheet Continuum Circuits */}
                    <div>
                      <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                        <Layers className="w-3.5 h-3.5 text-emerald-600" />
                        Unified Project Circuits ({topology.circuits.length})
                      </h4>
                      <div className="space-y-2">
                        {topology.circuits.map((uc) => (
                          <div key={uc.circuit_code} className="p-2.5 bg-slate-50 border border-slate-200 rounded-lg text-xs">
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-bold text-slate-900">{uc.circuit_code}</span>
                              <span className="text-[10px] font-semibold bg-emerald-100 text-emerald-800 px-1.5 py-0.5 rounded">
                                {uc.fluid} - {uc.material || 'General'}
                              </span>
                            </div>
                            <div className="text-[11px] text-slate-600 flex justify-between">
                              <span>Sheets: <b>{uc.sheet_ids.length}</b></span>
                              <span>Pipes: <b>{uc.total_pipes}</b></span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            ) : !result ? (
              <div className="p-8 text-center text-slate-400 text-xs">
                Click &quot;Detect P&amp;ID&quot; to digitize components and generate corrosion circuits.
              </div>
            ) : mode === 'digitize' ? (
              <div className="flex flex-col h-full">
                <div className="divide-y divide-slate-100 flex-1 overflow-y-auto">
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
                          <div className="flex items-center space-x-1.5">
                            {p.manual && (
                              <span className="text-[9px] bg-purple-100 text-purple-700 font-bold px-1.5 py-0.5 rounded">
                                OVERRIDDEN
                              </span>
                            )}
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
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingPid(p);
                              }}
                              className="p-1 hover:bg-slate-200 text-slate-500 hover:text-indigo-600 rounded transition"
                              title="Edit / Override Line Data"
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                        <div className="mt-1 flex items-center space-x-3 text-slate-500 text-[11px]">
                          <span>Fluid: <b className="text-slate-700">{p.fluid || '—'}</b></span>
                          <span>Class: <b className="text-slate-700">{p.pclass || '—'}</b></span>
                          <span>Size: <b className="text-slate-700">{p.size || '—'}</b></span>
                        </div>
                        {(p.operating_temp_c != null || p.operating_press_barg != null || p.corrosion_loop) && (
                          <div className="mt-1 pt-1 border-t border-slate-100 flex items-center space-x-2 text-[10px] text-slate-500">
                            {p.operating_temp_c != null && <span>T: <b className="text-slate-700">{p.operating_temp_c}°C</b></span>}
                            {p.operating_press_barg != null && <span>P: <b className="text-slate-700">{p.operating_press_barg} barg</b></span>}
                            {p.corrosion_loop && <span>Loop: <b className="text-indigo-600">{p.corrosion_loop}</b></span>}
                          </div>
                        )}
                      </div>
                    ))}
                </div>

                {/* Off-Page Connectors Section */}
                {result.opcs && result.opcs.length > 0 && (
                  <div className="p-3 bg-slate-50 border-t border-slate-200">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[11px] font-bold text-slate-700 uppercase tracking-wider flex items-center gap-1.5">
                        <ExternalLink className="w-3.5 h-3.5 text-indigo-600" />
                        Off-Page Connectors ({result.opcs.length})
                      </span>
                    </div>
                    <div className="space-y-2 max-h-48 overflow-y-auto">
                      {result.opcs.map((opc, oIdx) => (
                        <div
                          key={oIdx}
                          className="p-2 bg-white border border-slate-200 rounded-lg shadow-sm text-xs flex flex-col gap-1"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-slate-800 truncate">
                              {opc.line_number || opc.text || `OPC #${oIdx + 1}`}
                            </span>
                            <span
                              className={`text-[9px] font-extrabold px-1.5 py-0.5 rounded uppercase ${
                                opc.direction === 'incoming'
                                  ? 'bg-blue-100 text-blue-700'
                                  : 'bg-indigo-100 text-indigo-700'
                              }`}
                            >
                              {opc.direction}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-500 flex items-center justify-between">
                            <span>Target: <b className="text-slate-700">{opc.target_drawing || opc.target_sheet_number || 'Unknown'}</b></span>
                            <button
                              onClick={() => handleSelectOpc(opc)}
                              className="px-2 py-0.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded text-[10px] font-semibold flex items-center gap-1 transition"
                            >
                              <span>Navigate</span>
                              <ArrowRight className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
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
                    <div key={c.code} className="border border-slate-200 rounded-xl p-3 bg-slate-50 space-y-2">
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
                      <div className="text-xs text-slate-600">
                        <div>Piping Classes: <b>{c.classes.join(', ') || '—'}</b></div>
                        <div>Associated Lines: <b>{c.pid_idxs.length} line(s)</b></div>
                      </div>

                      {/* API RP 970 Operating Summary */}
                      {c.operating_summary && (
                        <div className="pt-1 border-t border-slate-200/80 text-[11px] grid grid-cols-2 gap-1 text-slate-600">
                          <div>Avg Temp: <b className="text-slate-800">{c.operating_summary.avg_temperature_c != null ? `${c.operating_summary.avg_temperature_c} °C` : '—'}</b></div>
                          <div>Avg Press: <b className="text-slate-800">{c.operating_summary.avg_pressure_barg != null ? `${c.operating_summary.avg_pressure_barg} barg` : '—'}</b></div>
                          <div>Min CA: <b className="text-slate-800">{c.operating_summary.corrosion_allowance_mm != null ? `${c.operating_summary.corrosion_allowance_mm} mm` : '—'}</b></div>
                          <div>Loop: <b className="text-indigo-700">{c.operating_summary.corrosion_loop || '—'}</b></div>
                        </div>
                      )}

                      {/* Provenance / Audit Trail Card */}
                      {c.provenance && (
                        <div className="mt-1 p-2 bg-white rounded-lg border border-slate-200 text-[10px] space-y-0.5 text-slate-500">
                          <div className="flex items-center justify-between font-medium text-slate-700">
                            <span className="flex items-center gap-1">
                              <ShieldCheck className="w-3 h-3 text-emerald-600" />
                              Rule: {c.provenance.rule}
                            </span>
                            <span className="text-emerald-700 font-bold">
                              {(c.provenance.confidence * 100).toFixed(0)}% Conf
                            </span>
                          </div>
                          <div>Evidence: {c.provenance.evidence}</div>
                        </div>
                      )}
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

      {/* Line List Import Modal */}
      {showLineListModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-2">
                <FileSpreadsheet className="w-5 h-5 text-emerald-600" />
                <h3 className="font-bold text-slate-900 text-base">Import Line List Register</h3>
              </div>
              <button
                onClick={() => setShowLineListModal(false)}
                className="p-1 text-slate-400 hover:text-slate-600 rounded-lg transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              Upload an engineering Line List spreadsheet (.xlsx or .csv) containing operating conditions
              (Design Temp, Press, Corrosion Allowance, Fluid Phase, Material, Corrosion Loop).
              Lines in your P&amp;IDs will automatically be correlated and enriched according to API RP 970.
            </p>

            <div className="border-2 border-dashed border-slate-300 hover:border-indigo-500 rounded-xl p-6 text-center transition bg-slate-50/50">
              <Upload className="w-8 h-8 text-slate-400 mx-auto mb-2" />
              <label className="block text-xs font-semibold text-indigo-600 hover:text-indigo-700 cursor-pointer">
                <span>Select .xlsx or .csv File</span>
                <input
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  onChange={handleUploadLineListFile}
                  disabled={uploadingLineList}
                  className="hidden"
                />
              </label>
              <p className="text-[11px] text-slate-400 mt-1">Excel or CSV with Line Tag header</p>
            </div>

            {uploadingLineList && (
              <div className="flex items-center justify-center space-x-2 text-xs text-indigo-600 font-medium">
                <RefreshCw className="w-4 h-4 animate-spin" />
                <span>Parsing and enriching line operating parameters...</span>
              </div>
            )}

            <div className="flex justify-end pt-2">
              <button
                onClick={() => setShowLineListModal(false)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Engineer Manual Review / Edit Modal */}
      {editingPid && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-2">
                <Edit3 className="w-5 h-5 text-indigo-600" />
                <h3 className="font-bold text-slate-900 text-base">Engineer Line Review &amp; Override</h3>
              </div>
              <button
                onClick={() => setEditingPid(null)}
                className="p-1 text-slate-400 hover:text-slate-600 rounded-lg transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-slate-500">
              Manual overrides take highest precedence in API RP 970 circuit assignment and are logged in the engineering audit trail.
            </p>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Line Tag</label>
                <input
                  type="text"
                  value={editingPid.pid}
                  onChange={(e) => setEditingPid({ ...editingPid, pid: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Fluid Code</label>
                <input
                  type="text"
                  value={editingPid.fluid}
                  onChange={(e) => setEditingPid({ ...editingPid, fluid: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Piping Class</label>
                <input
                  type="text"
                  value={editingPid.pclass}
                  onChange={(e) => setEditingPid({ ...editingPid, pclass: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Nominal Size</label>
                <input
                  type="text"
                  value={editingPid.size || ''}
                  onChange={(e) => setEditingPid({ ...editingPid, size: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Fluid Phase</label>
                <input
                  type="text"
                  placeholder="e.g. Liquid, Vapor, Two-Phase"
                  value={editingPid.fluid_phase || ''}
                  onChange={(e) => setEditingPid({ ...editingPid, fluid_phase: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Material</label>
                <input
                  type="text"
                  placeholder="e.g. CS, SS316, Alloy"
                  value={editingPid.material || ''}
                  onChange={(e) => setEditingPid({ ...editingPid, material: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Operating Temp (°C)</label>
                <input
                  type="number"
                  step="any"
                  value={editingPid.operating_temp_c ?? ''}
                  onChange={(e) =>
                    setEditingPid({
                      ...editingPid,
                      operating_temp_c: e.target.value === '' ? undefined : parseFloat(e.target.value),
                    })
                  }
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Operating Press (barg)</label>
                <input
                  type="number"
                  step="any"
                  value={editingPid.operating_press_barg ?? ''}
                  onChange={(e) =>
                    setEditingPid({
                      ...editingPid,
                      operating_press_barg: e.target.value === '' ? undefined : parseFloat(e.target.value),
                    })
                  }
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Corrosion Allowance (mm)</label>
                <input
                  type="number"
                  step="any"
                  value={editingPid.corrosion_allowance_mm ?? ''}
                  onChange={(e) =>
                    setEditingPid({
                      ...editingPid,
                      corrosion_allowance_mm: e.target.value === '' ? undefined : parseFloat(e.target.value),
                    })
                  }
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
              <div>
                <label className="font-semibold text-slate-700 block mb-1">Corrosion Loop Code</label>
                <input
                  type="text"
                  placeholder="e.g. CL-101"
                  value={editingPid.corrosion_loop || ''}
                  onChange={(e) => setEditingPid({ ...editingPid, corrosion_loop: e.target.value })}
                  className="w-full p-2 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 text-xs"
                />
              </div>
            </div>

            <div className="flex justify-end space-x-2 pt-3 border-t border-slate-100">
              <button
                onClick={() => setEditingPid(null)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveEditedPid}
                disabled={savingEdit}
                className="px-4 py-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition flex items-center space-x-1.5"
              >
                {savingEdit ? (
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                <span>Save Overrides</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
