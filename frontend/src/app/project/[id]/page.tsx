'use client';

import { useState, useEffect, useRef } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowLeft,
  Play,
  Download,
  CheckCircle2,
  Trash2,
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
  Crosshair,
  Eye,
  Component,
} from 'lucide-react';
import {
  ProjectResponse,
  SheetResponse,
  DigitizationResult,
  CorrosionSystem,
  CorrosionCircuit,
  ValidationReport,
  PipingID,
  OffPageConnector,
  SymbolDetection,
  ProjectTopologyResponse,
} from '@/types/schema';
import {
  fetchProject,
  fetchResult,
  fetchSystems,
  fetchValidation,
  triggerDetection,
  getRawImageUrl,
  deleteSheet,
  getMarkedImageUrl,
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

  const [showOverlay, setShowOverlay] = useState<boolean>(true);
  const [detecting, setDetecting] = useState(false);
  const [progressMsg, setProgressMsg] = useState('');
  const [progressPct, setProgressPct] = useState(0);

  const [selectedPidIdx, setSelectedPidIdx] = useState<number | null>(null);
  const [selectedSystemIdx, setSelectedSystemIdx] = useState<number | null>(null);
  const [selectedCircuitCode, setSelectedCircuitCode] = useState<string | null>(null);
  const [digitizeSubTab, setDigitizeSubTab] = useState<'lines' | 'symbols' | 'opcs'>('lines');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterFluid, setFilterFluid] = useState<string>('all');

  // Line list modal state
  const [showLineListModal, setShowLineListModal] = useState(false);
  const [uploadingLineList, setUploadingLineList] = useState(false);

  // Engineer Edit modal state
  const [editingPid, setEditingPid] = useState<PipingID | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [showDeleteSheetModal, setShowDeleteSheetModal] = useState(false);
  const [deletingSheet, setDeletingSheet] = useState(false);

  const canvasRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const osdModuleRef = useRef<any>(null);

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
      osdModuleRef.current = OpenSeadragon.default || OpenSeadragon;

      if (viewerRef.current) {
        viewerRef.current.destroy();
      }

      const isDetected = activeSheet?.status === 'detected' || Boolean(result);
      const initialUrl = showOverlay && isDetected
        ? getMarkedImageUrl(projectId, activeSheet.id, mode === 'circuit' ? 'circuit' : 'system')
        : getRawImageUrl(projectId, activeSheet.id);

      viewer = OpenSeadragon.default({
        element: canvasRef.current,
        prefixUrl: 'https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.1/images/',
        tileSources: {
          type: 'image',
          url: initialUrl,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, activeSheet]);

  // Dynamically swap between Raw P&ID and Marked Overlay while preserving viewport
  const isDetected = activeSheet?.status === 'detected' || Boolean(result);
  useEffect(() => {
    if (!viewerRef.current || !activeSheet || !projectId) return;
    const viewer = viewerRef.current;
    if (!viewer.viewport) return;

    const targetUrl = showOverlay && isDetected
      ? getMarkedImageUrl(projectId, activeSheet.id, mode === 'circuit' ? 'circuit' : 'system')
      : getRawImageUrl(projectId, activeSheet.id);

    const bounds = viewer.viewport.getBounds();
    viewer.open({
      type: 'image',
      url: targetUrl,
    });
    const onOpen = () => {
      if (viewer.viewport && bounds) {
        viewer.viewport.fitBounds(bounds, true);
      }
      viewer.removeHandler('open', onOpen);
    };
    viewer.addHandler('open', onOpen);
  }, [showOverlay, mode, isDetected, projectId, activeSheet]);

  // Zoom & Pan to Bounding Box on OpenSeadragon Canvas with High-Visibility Overlay
  const zoomToBbox = (minX: number, minY: number, maxX: number, maxY: number) => {
    if (!viewerRef.current || !viewerRef.current.viewport) return;
    const viewer = viewerRef.current;
    const w = Math.max(maxX - minX, 60);
    const h = Math.max(maxY - minY, 60);

    // Padding around target for comfortable view (min 160px)
    const padX = Math.max(w * 0.45, 160);
    const padY = Math.max(h * 0.45, 160);
    const targetX = Math.max(0, minX - padX);
    const targetY = Math.max(0, minY - padY);
    const targetW = w + padX * 2;
    const targetH = h + padY * 2;

    try {
      let viewRect: any = null;
      let targetRect: any = null;
      const OSD = osdModuleRef.current || (window as any).OpenSeadragon;

      if (viewer.viewport.imageToViewportRectangle) {
        viewRect = viewer.viewport.imageToViewportRectangle(targetX, targetY, targetW, targetH);
        targetRect = viewer.viewport.imageToViewportRectangle(minX, minY, maxX - minX, maxY - minY);
      } else if (OSD && OSD.Rect) {
        const item = viewer.world?.getItemAt(0);
        const contentSize = item?.getContentSize() || { x: activeSheet?.width || 3000, y: activeSheet?.height || 2000 };
        const normX = targetX / contentSize.x;
        const normY = (targetY / contentSize.y) * (contentSize.y / contentSize.x);
        const normW = targetW / contentSize.x;
        const normH = (targetH / contentSize.y) * (contentSize.y / contentSize.x);
        viewRect = new OSD.Rect(normX, normY, normW, normH);

        const tNormX = minX / contentSize.x;
        const tNormY = (minY / contentSize.y) * (contentSize.y / contentSize.x);
        const tNormW = (maxX - minX) / contentSize.x;
        const tNormH = ((maxY - minY) / contentSize.y) * (contentSize.y / contentSize.x);
        targetRect = new OSD.Rect(tNormX, tNormY, tNormW, tNormH);
      }

      if (viewRect) {
        viewer.viewport.fitBounds(viewRect, false);
      }

      // Add high-visibility glowing target overlay on the exact feature
      if (targetRect && viewer.clearOverlays && viewer.addOverlay) {
        viewer.clearOverlays();
        const highlightEl = document.createElement('div');
        highlightEl.style.border = '3px solid #6366f1'; // Indigo-500
        highlightEl.style.backgroundColor = 'rgba(99, 102, 241, 0.18)';
        highlightEl.style.borderRadius = '8px';
        highlightEl.style.boxShadow = '0 0 25px rgba(99, 102, 241, 0.8), inset 0 0 15px rgba(99, 102, 241, 0.3)';
        highlightEl.style.pointerEvents = 'none';
        highlightEl.style.zIndex = '50';
        highlightEl.className = 'animate-pulse';

        viewer.addOverlay({
          element: highlightEl,
          location: targetRect,
        });
      }
    } catch (e) {
      console.error('Failed to zoom to bbox:', e);
    }
  };

  const handleSelectPipingId = (p: PipingID, idx: number) => {
    setSelectedPidIdx(idx);
    if (!result) return;
    const allXs = [p.x1, p.x2];
    const allYs = [p.y1, p.y2];

    if (p.run_idx >= 0 && result.runs && result.runs[p.run_idx]) {
      const r = result.runs[p.run_idx];
      if (r.points && r.points.length > 0) {
        for (const pt of r.points) {
          allXs.push(pt[0]);
          allYs.push(pt[1]);
        }
      } else {
        allXs.push(r.x1, r.x2);
        allYs.push(r.y1, r.y2);
      }
    }

    if (p.extra_runs && p.extra_runs.length > 0 && result.runs) {
      for (const erIdx of p.extra_runs) {
        const er = result.runs[erIdx];
        if (er) {
          if (er.points && er.points.length > 0) {
            for (const pt of er.points) {
              allXs.push(pt[0]);
              allYs.push(pt[1]);
            }
          } else {
            allXs.push(er.x1, er.x2);
            allYs.push(er.y1, er.y2);
          }
        }
      }
    }

    zoomToBbox(
      Math.min(...allXs),
      Math.min(...allYs),
      Math.max(...allXs),
      Math.max(...allYs)
    );
  };

  const handleFocusOpc = (opc: OffPageConnector) => {
    zoomToBbox(opc.x1, opc.y1, opc.x2, opc.y2);
  };

  const handleSelectSymbol = (s: SymbolDetection) => {
    zoomToBbox(s.x1, s.y1, s.x2, s.y2);
  };

  const handleSelectSystem = (s: CorrosionSystem, idx: number) => {
    setSelectedSystemIdx(idx);
    if (!result) return;
    const allXs: number[] = [];
    const allYs: number[] = [];

    for (const rIdx of s.run_idxs || []) {
      const r = result.runs?.[rIdx];
      if (r) {
        if (r.points && r.points.length > 0) {
          for (const pt of r.points) {
            allXs.push(pt[0]);
            allYs.push(pt[1]);
          }
        } else {
          allXs.push(r.x1, r.x2);
          allYs.push(r.y1, r.y2);
        }
      }
    }

    for (const pIdx of s.pid_idxs || []) {
      const p = result.piping_ids?.[pIdx];
      if (p) {
        allXs.push(p.x1, p.x2);
        allYs.push(p.y1, p.y2);
      }
    }

    if (allXs.length > 0 && allYs.length > 0) {
      zoomToBbox(
        Math.min(...allXs),
        Math.min(...allYs),
        Math.max(...allXs),
        Math.max(...allYs)
      );
    }
  };

  const handleSelectCircuit = (c: CorrosionCircuit) => {
    setSelectedCircuitCode(c.code);
    if (!result) return;
    const allXs: number[] = [];
    const allYs: number[] = [];

    for (const rIdx of c.run_idxs || []) {
      const r = result.runs?.[rIdx];
      if (r) {
        if (r.points && r.points.length > 0) {
          for (const pt of r.points) {
            allXs.push(pt[0]);
            allYs.push(pt[1]);
          }
        } else {
          allXs.push(r.x1, r.x2);
          allYs.push(r.y1, r.y2);
        }
      }
    }

    for (const pIdx of c.pid_idxs || []) {
      const p = result.piping_ids?.[pIdx];
      if (p) {
        allXs.push(p.x1, p.x2);
        allYs.push(p.y1, p.y2);
      }
    }

    if (allXs.length > 0 && allYs.length > 0) {
      zoomToBbox(
        Math.min(...allXs),
        Math.min(...allYs),
        Math.max(...allXs),
        Math.max(...allYs)
      );
    }
  };

  const handleFocusFlag = (f: Record<string, any>) => {
    if (!result) return;
    if (f.pid) {
      const target = result.piping_ids.find((p) => p.pid === f.pid);
      if (target) {
        handleSelectPipingId(target, result.piping_ids.indexOf(target));
        return;
      }
    }
    if (f.x1 != null && f.y1 != null && f.x2 != null && f.y2 != null) {
      zoomToBbox(f.x1, f.y1, f.x2, f.y2);
      return;
    }
    if (f.run_idx != null && result.runs?.[f.run_idx]) {
      const r = result.runs[f.run_idx];
      zoomToBbox(r.x1, r.y1, r.x2, r.y2);
    }
  };

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

      let completedHandled = false;
      let pollInterval: any = null;

      const onCompleted = () => {
        if (completedHandled) return;
        completedHandled = true;
        if (pollInterval) clearInterval(pollInterval);
        try { ws.close(); } catch (e) {}
        setDetecting(false);
        setProgressPct(100);
        setProgressMsg('Digitasi & Sistemisasi selesai.');

        // Refresh project and active sheet state
        fetchProject(projectId).then((p) => {
          setProject(p);
          const updatedSheet = p.sheets?.find((sh) => sh.id === activeSheet.id);
          if (updatedSheet) {
            setActiveSheet(updatedSheet);
          }
        });
        fetchResult(projectId, activeSheet.id).then(setResult).catch(console.error);
        fetchSystems(projectId, activeSheet.id).then(setSystems).catch(console.error);
        fetchValidation(projectId, activeSheet.id).then(setValidation).catch(console.error);
      };

      // Periodic polling fallback in case WebSocket drops or times out
      pollInterval = setInterval(async () => {
        try {
          const res = await fetchProject(projectId);
          const s = res.sheets?.find((sh) => sh.id === activeSheet.id);
          if (s && s.status === 'detected') {
            onCompleted();
          }
        } catch (e) {}
      }, 3000);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.pct !== undefined) setProgressPct(data.pct);
          if (data.message) setProgressMsg(data.message);
          if (data.step === 'completed') {
            onCompleted();
          } else if (data.step === 'failed') {
            if (pollInterval) clearInterval(pollInterval);
            ws.close();
            setDetecting(false);
            alert('Detection error: ' + data.message);
          }
        } catch (err) {}
      };

      ws.onerror = () => {
        // WebSocket error, fallback polling continues to monitor progress
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

  const handleDeleteActiveSheet = async () => {
    if (!projectId || !activeSheet) return;
    try {
      setDeletingSheet(true);
      await deleteSheet(projectId, activeSheet.id);
      setShowDeleteSheetModal(false);
      const updated = await fetchProject(projectId);
      setProject(updated);
      if (updated.sheets && updated.sheets.length > 0) {
        setActiveSheet(updated.sheets[0]);
        router.push(`/project/${projectId}?sheetId=${updated.sheets[0].id}`);
      } else {
        router.push('/');
      }
    } catch (err: any) {
      alert('Failed to delete sheet: ' + err.message);
    } finally {
      setDeletingSheet(false);
    }
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
              {project?.sheets && project.sheets.length > 0 && activeSheet && (
                <div className="flex items-center space-x-1.5">
                  <select
                    value={activeSheet.id}
                    onChange={(e) => {
                      const sel = project.sheets?.find((s) => s.id === e.target.value);
                      if (sel) {
                        setActiveSheet(sel);
                        router.push(`/project/${projectId}?sheetId=${sel.id}`);
                      }
                    }}
                    className="text-xs bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-semibold px-2.5 py-1 rounded-lg border border-indigo-200 outline-none cursor-pointer max-w-[240px] truncate transition"
                  >
                    {project.sheets.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.filename} ({s.status})
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => setShowDeleteSheetModal(true)}
                    className="p-1 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                    title="Delete current P&ID Sheet"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
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

          {/* Circuit Overlay Toggle Button in Header Bar */}
          {(result || activeSheet?.status === 'detected') && (
            <button
              onClick={() => setShowOverlay(!showOverlay)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition ${
                showOverlay
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700 shadow'
                  : 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-50'
              }`}
              title="Toggle Color-Coded Circuit Marking on Canvas"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>{showOverlay ? 'Circuit Overlay: ON' : 'Circuit Overlay: OFF'}</span>
            </button>
          )}

          {/* Export Dropdown Menu */}
          {(result || activeSheet?.status === 'detected') && (
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
          <div className="absolute bottom-6 left-6 flex bg-white/95 backdrop-blur border border-slate-300 rounded-xl shadow-lg p-1 space-x-1 z-40">
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
            {(result || activeSheet?.status === 'detected') && (
              <>
                <div className="w-[1px] h-6 bg-slate-200 self-center my-auto mx-1" />
                <button
                  onClick={() => setShowOverlay(!showOverlay)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition ${
                    showOverlay
                      ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                  title="Toggle Color-Coded Circuit Marking on Canvas"
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span>{showOverlay ? 'Circuit Overlay: ON' : 'Circuit Overlay: OFF'}</span>
                </button>
              </>
            )}
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

          {/* Search bar for tables & Sub-tabs */}
          {mode === 'digitize' && (
            <div className="border-b border-slate-200 bg-slate-50">
              <div className="p-3">
                <div className="relative">
                  <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5" />
                  <input
                    type="text"
                    placeholder={
                      digitizeSubTab === 'lines'
                        ? 'Filter by line, fluid, or class...'
                        : digitizeSubTab === 'symbols'
                        ? 'Filter by tag, equipment, or valve...'
                        : 'Filter by connector or target...'
                    }
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-8 pr-3 py-1.5 bg-white border border-slate-200 rounded-lg text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                </div>
              </div>

              {/* Sub-Tabs: Lines / Symbols / OPCs */}
              {result && (
                <div className="flex border-t border-slate-200 bg-slate-100/70 p-1 text-[11px] font-semibold text-slate-600 gap-1">
                  <button
                    onClick={() => setDigitizeSubTab('lines')}
                    className={`flex-1 py-1 px-2 rounded-md transition text-center ${
                      digitizeSubTab === 'lines'
                        ? 'bg-white text-indigo-700 shadow-sm font-bold'
                        : 'hover:text-slate-900'
                    }`}
                  >
                    Lines ({result.piping_ids.length})
                  </button>
                  <button
                    onClick={() => setDigitizeSubTab('symbols')}
                    className={`flex-1 py-1 px-2 rounded-md transition text-center ${
                      digitizeSubTab === 'symbols'
                        ? 'bg-white text-indigo-700 shadow-sm font-bold'
                        : 'hover:text-slate-900'
                    }`}
                  >
                    Symbols ({result.symbols?.length || 0})
                  </button>
                  {result.opcs && result.opcs.length > 0 && (
                    <button
                      onClick={() => setDigitizeSubTab('opcs')}
                      className={`py-1 px-2 rounded-md transition text-center ${
                        digitizeSubTab === 'opcs'
                          ? 'bg-white text-indigo-700 shadow-sm font-bold'
                          : 'hover:text-slate-900'
                      }`}
                    >
                      OPCs ({result.opcs.length})
                    </button>
                  )}
                </div>
              )}
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
              <div className="flex flex-col h-full overflow-hidden">
                {/* Digitize Sub-tabs (Lines, Symbols, OPCs) */}
                <div className="flex border-b border-slate-200 bg-slate-50 px-3 pt-2 gap-1.5 shrink-0">
                  <button
                    onClick={() => setDigitizeSubTab('lines')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-t-lg transition border-b-2 flex items-center gap-1.5 ${
                      digitizeSubTab === 'lines'
                        ? 'border-indigo-600 text-indigo-700 bg-white shadow-sm'
                        : 'border-transparent text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    <span>Lines</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-100 text-slate-600 font-semibold">
                      {result.piping_ids?.length || 0}
                    </span>
                  </button>
                  <button
                    onClick={() => setDigitizeSubTab('symbols')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-t-lg transition border-b-2 flex items-center gap-1.5 ${
                      digitizeSubTab === 'symbols'
                        ? 'border-indigo-600 text-indigo-700 bg-white shadow-sm'
                        : 'border-transparent text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    <span>Symbols</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-100 text-slate-600 font-semibold">
                      {result.symbols?.length || 0}
                    </span>
                  </button>
                  <button
                    onClick={() => setDigitizeSubTab('opcs')}
                    className={`px-3 py-1.5 text-xs font-bold rounded-t-lg transition border-b-2 flex items-center gap-1.5 ${
                      digitizeSubTab === 'opcs'
                        ? 'border-indigo-600 text-indigo-700 bg-white shadow-sm'
                        : 'border-transparent text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    <span>OPCs</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-100 text-slate-600 font-semibold">
                      {result.opcs?.length || 0}
                    </span>
                  </button>
                </div>

                {/* Sub-tab 1: Piping Lines */}
                {digitizeSubTab === 'lines' && (
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
                          onClick={() => handleSelectPipingId(p, idx)}
                          className={`p-3 cursor-pointer transition text-xs group ${
                            selectedPidIdx === idx
                              ? 'bg-indigo-50 border-l-4 border-indigo-600'
                              : 'hover:bg-slate-50'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center space-x-1.5">
                              <Crosshair className="w-3.5 h-3.5 text-slate-400 group-hover:text-indigo-600 transition shrink-0" />
                              <span className="font-bold text-slate-900 group-hover:text-indigo-700 transition">
                                {p.pid}
                              </span>
                            </div>
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
                )}

                {/* Sub-tab 2: Symbols / Valves / Equipment */}
                {digitizeSubTab === 'symbols' && (
                  <div className="divide-y divide-slate-100 flex-1 overflow-y-auto">
                    {result.symbols && result.symbols.length > 0 ? (
                      result.symbols
                        .filter((s) => {
                          if (!searchQuery) return true;
                          const q = searchQuery.toLowerCase();
                          return (
                            s.cls.toLowerCase().includes(q) ||
                            s.coarse.toLowerCase().includes(q) ||
                            (s.tag && s.tag.toLowerCase().includes(q))
                          );
                        })
                        .map((s, sIdx) => {
                          const coarseColor =
                            s.coarse === 'valve'
                              ? 'bg-emerald-100 text-emerald-800'
                              : s.coarse === 'equipment'
                              ? 'bg-purple-100 text-purple-800'
                              : s.coarse === 'instrument'
                              ? 'bg-blue-100 text-blue-800'
                              : 'bg-slate-100 text-slate-700';

                          return (
                            <div
                              key={sIdx}
                              onClick={() => handleSelectSymbol(s)}
                              className="p-3 cursor-pointer hover:bg-indigo-50/60 transition text-xs group"
                            >
                              <div className="flex items-center justify-between">
                                <div className="flex items-center space-x-2 truncate">
                                  <Crosshair className="w-3.5 h-3.5 text-slate-400 group-hover:text-indigo-600 transition shrink-0" />
                                  <span className="font-bold text-slate-900 group-hover:text-indigo-700 truncate capitalize">
                                    {s.cls.replace(/_/g, ' ')}
                                  </span>
                                  {s.tag && (
                                    <span className="font-mono text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                                      {s.tag}
                                    </span>
                                  )}
                                </div>
                                <div className="flex items-center space-x-1.5 shrink-0">
                                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded capitalize ${coarseColor}`}>
                                    {s.coarse}
                                  </span>
                                  <span className="text-[10px] font-mono text-slate-400">
                                    {(s.conf * 100).toFixed(0)}%
                                  </span>
                                </div>
                              </div>
                              <div className="mt-1 text-[11px] text-slate-400 flex items-center justify-between">
                                <span>Box: [{Math.round(s.x1)}, {Math.round(s.y1)}] – [{Math.round(s.x2)}, {Math.round(s.y2)}]</span>
                                <span className="text-[10px] text-indigo-600 font-medium group-hover:underline">
                                  Click to Zoom
                                </span>
                              </div>
                            </div>
                          );
                        })
                    ) : (
                      <div className="p-8 text-center text-slate-400 text-xs">
                        No symbols detected on this sheet.
                      </div>
                    )}
                  </div>
                )}

                {/* Sub-tab 3: Off-Page Connectors */}
                {digitizeSubTab === 'opcs' && (
                  <div className="divide-y divide-slate-100 flex-1 overflow-y-auto">
                    {result.opcs && result.opcs.length > 0 ? (
                      result.opcs
                        .filter((opc) => {
                          if (!searchQuery) return true;
                          const q = searchQuery.toLowerCase();
                          return (
                            (opc.line_number && opc.line_number.toLowerCase().includes(q)) ||
                            (opc.text && opc.text.toLowerCase().includes(q)) ||
                            (opc.target_drawing && opc.target_drawing.toLowerCase().includes(q)) ||
                            (opc.target_sheet_number && opc.target_sheet_number.toLowerCase().includes(q))
                          );
                        })
                        .map((opc, oIdx) => (
                          <div
                            key={oIdx}
                            onClick={() => handleFocusOpc(opc)}
                            className="p-3 cursor-pointer hover:bg-indigo-50/60 transition text-xs flex flex-col gap-1.5 group"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center space-x-1.5 truncate">
                                <Crosshair className="w-3.5 h-3.5 text-slate-400 group-hover:text-indigo-600 transition shrink-0" />
                                <span className="font-bold text-slate-900 group-hover:text-indigo-700 truncate">
                                  {opc.line_number || opc.text || `OPC #${oIdx + 1}`}
                                </span>
                              </div>
                              <span
                                className={`text-[9px] font-extrabold px-1.5 py-0.5 rounded uppercase shrink-0 ${
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
                              <div className="flex items-center space-x-1">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleSelectOpc(opc);
                                  }}
                                  className="px-2 py-0.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded text-[10px] font-semibold flex items-center gap-1 transition"
                                  title="Open Target P&ID Sheet"
                                >
                                  <span>Open Drawing</span>
                                  <ArrowRight className="w-3 h-3" />
                                </button>
                              </div>
                            </div>
                          </div>
                        ))
                    ) : (
                      <div className="p-8 text-center text-slate-400 text-xs">
                        No off-page connectors detected on this sheet.
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : mode === 'system' ? (
              <div className="p-4 space-y-3 overflow-y-auto h-full">
                {systems.map((s) => (
                  <div
                    key={s.fluid}
                    onClick={() => handleSelectSystem(s, s.index)}
                    className={`border rounded-xl p-3 cursor-pointer transition ${
                      selectedSystemIdx === s.index
                        ? 'border-indigo-600 bg-indigo-50/80 shadow-sm ring-2 ring-indigo-500/20'
                        : 'border-slate-200 bg-slate-50 hover:bg-slate-100/80'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span
                          className="w-3.5 h-3.5 rounded shadow-sm"
                          style={{ backgroundColor: `rgb(${s.color.join(',')})` }}
                        />
                        <span className="font-bold text-slate-900 text-sm">
                          System #{String(s.index).padStart(2, '0')}
                        </span>
                      </div>
                      <div className="flex items-center space-x-1.5">
                        <span className="font-bold text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded text-xs">
                          {s.fluid}
                        </span>
                        <Crosshair className="w-3.5 h-3.5 text-slate-400" />
                      </div>
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
              <div className="p-4 space-y-3 overflow-y-auto h-full">
                {systems.flatMap((s) =>
                  s.circuits.map((c) => (
                    <div
                      key={c.code}
                      onClick={() => handleSelectCircuit(c)}
                      className={`border rounded-xl p-3 cursor-pointer transition space-y-2 ${
                        selectedCircuitCode === c.code
                          ? 'border-indigo-600 bg-indigo-50/80 shadow-sm ring-2 ring-indigo-500/20'
                          : 'border-slate-200 bg-slate-50 hover:bg-slate-100/80'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <span
                            className="w-3.5 h-3.5 rounded shadow-sm"
                            style={{ backgroundColor: `rgb(${c.color.join(',')})` }}
                          />
                          <span className="font-bold text-slate-900 text-sm">Circuit {c.code}</span>
                        </div>
                        <div className="flex items-center space-x-1.5">
                          <span className="font-semibold text-slate-700 bg-white border border-slate-200 px-2 py-0.5 rounded text-xs">
                            {s.fluid} - {c.material || 'General'}
                          </span>
                          <Crosshair className="w-3.5 h-3.5 text-slate-400" />
                        </div>
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
              <div className="p-4 space-y-4 overflow-y-auto h-full">
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
                            onClick={() => handleFocusFlag(f)}
                            className="p-2.5 bg-amber-50 hover:bg-amber-100/80 border border-amber-200 rounded-lg text-xs text-amber-900 cursor-pointer transition flex flex-col gap-1 group"
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-bold uppercase text-[10px] bg-amber-200 px-1.5 py-0.5 rounded text-amber-800">
                                {f.kind}
                              </span>
                              <span className="text-[10px] text-amber-700 flex items-center gap-1 opacity-80 group-hover:opacity-100">
                                <Crosshair className="w-3 h-3" />
                                <span>Zoom</span>
                              </span>
                            </div>
                            <p className="text-[11px] leading-relaxed">
                              {f.msg_en || f.msg}
                            </p>
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
      {/* Delete Sheet Confirmation Modal */}
      {showDeleteSheetModal && activeSheet && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4 border border-rose-100">
            <div className="flex items-center space-x-3 text-rose-600">
              <div className="w-10 h-10 rounded-xl bg-rose-50 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-900">Delete P&ID Drawing Sheet?</h3>
                <p className="text-xs text-slate-500">This action cannot be undone.</p>
              </div>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              Are you sure you want to delete drawing sheet{' '}
              <strong className="text-slate-900 font-semibold">{activeSheet.filename}</strong>?
              Raw diagram tiles and all associated corrosion line circuit data for this sheet will be permanently deleted.
            </p>

            <div className="flex justify-end space-x-2 pt-3 border-t border-slate-100">
              <button
                type="button"
                disabled={deletingSheet}
                onClick={() => setShowDeleteSheetModal(false)}
                className="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg transition"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deletingSheet}
                onClick={handleDeleteActiveSheet}
                className="px-4 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white rounded-lg transition shadow flex items-center space-x-1.5"
              >
                {deletingSheet && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>{deletingSheet ? 'Deleting...' : 'Delete Sheet'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
