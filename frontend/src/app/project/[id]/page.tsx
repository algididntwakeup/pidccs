'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
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
  RotateCcw,
  RotateCw,
  Save,
  Sliders,
  Scissors,
  EyeOff,
  Tag,
  Palette,
  CheckSquare,
  Square,
  ChevronDown,
  Zap,
  Bot,
  Sparkles,
} from 'lucide-react';
import InteractivePipeCanvas from '@/components/InteractivePipeCanvas';
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
  PipeRun,
} from '@/types/schema';
import {
  fetchProject,
  fetchResult,
  fetchSystems,
  fetchValidation,
  triggerDetection,
  triggerEnrichment,
  fetchLatestJobForSheet,
  fetchJob,
  getRawImageUrl,
  deleteSheet,
  getExportUrl,
  uploadLineList,
  fetchProjectTopology,
  patchResult,
  splitRun,
  updateRunColor,
  batchUpdateRunColors,
  deleteRun,
  batchDeleteRuns,
  updateRunPoints,
  traceRegion,
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
  // Always-current mirror of result.runs. Declared immediately after the result
  // state (and BEFORE the selection state below) so that the ID<->index translation
  // in setSelectedRunIndices sees the freshest runs even when it is called in the
  // same tick as setResult (e.g. right after a split).
  const runsRef = useRef<PipeRun[]>([]);
  runsRef.current = result?.runs || [];
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
  const [digitizeSubTab, setDigitizeSubTab] = useState<'lines' | 'pipes' | 'symbols' | 'opcs'>('lines');
  const [searchQuery, setSearchQuery] = useState('');
  const [editingRunLabel, setEditingRunLabel] = useState<string>('');
  const runRowRefs = useRef<{ [key: number]: HTMLDivElement | null }>({});
  const [filterFluid, setFilterFluid] = useState<string>('all');

  // Line list modal state
  const [showLineListModal, setShowLineListModal] = useState(false);
  const [uploadingLineList, setUploadingLineList] = useState(false);

  // Engineer Edit modal state
  const [editingPid, setEditingPid] = useState<PipingID | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [showDeleteSheetModal, setShowDeleteSheetModal] = useState(false);
  const [deletingSheet, setDeletingSheet] = useState(false);
  const [detectDropdownOpen, setDetectDropdownOpen] = useState(false);
  const detectDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (detectDropdownRef.current && !detectDropdownRef.current.contains(event.target as Node)) {
        setDetectDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Interactive Pipe Canvas & Tooling state (Phase B.5)
  //
  // Selection is tracked BY RUN ID (string) as the source of truth, not by array
  // index. The backend REINDEXES runs on split/delete/re-scan, so an index-based
  // selection can silently start pointing at a DIFFERENT run after the array shifts
  // — leaving a stuck orange halo + control points on a pipe the user never selected.
  // IDs are stable across reindexing, so we resolve IDs -> current indices at render.
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set());
  const [splitMode, setSplitMode] = useState<boolean>(false);
  const [traceTool, setTraceTool] = useState<'pan' | 'rescan' | 'pen' | 'multiselect'>('pan');
  const [traceOpacity, setTraceOpacity] = useState<number>(0.85);
  const [history, setHistory] = useState<
    Array<{
      desc: string;
      prevRuns: PipeRun[];
      nextRuns: PipeRun[];
      prevPids: PipingID[];
      nextPids: PipingID[];
    }>
  >([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(false);
  const [savingChanges, setSavingChanges] = useState<boolean>(false);
  const [statusToast, setStatusToast] = useState<string | null>(null);
  const [viewerReady, setViewerReady] = useState(false);

  // Derived index selection (what the canvas + sidebar consume). Resolved from the
  // stable ID set against the CURRENT runs array, dropping any id that no longer
  // exists — this is what guarantees the orange halo can never outlive its pipe.
  const selectedRunIndices = useMemo<Set<number>>(() => {
    if (selectedRunIds.size === 0) return new Set<number>();
    const runs = result?.runs || [];
    const next = new Set<number>();
    runs.forEach((r, i) => {
      if (r && r.id && selectedRunIds.has(r.id)) next.add(i);
    });
    return next;
  }, [selectedRunIds, result?.runs]);

  // Compatibility setter: existing call sites pass a Set<number> of indices. We
  // translate those indices to stable run IDs so the selection survives reindexing.
  // It resolves against `runsRef` (always latest) rather than the render closure so
  // that a call made in the same tick as `setResult` sees the NEW runs array.
  const setSelectedRunIndices = useCallback(
    (value: Set<number> | ((prev: Set<number>) => Set<number>)) => {
      setSelectedRunIds((prevIds) => {
        const runs = runsRef.current;
        const prevIdx = new Set<number>();
        runs.forEach((r, i) => {
          if (r && r.id && prevIds.has(r.id)) prevIdx.add(i);
        });
        const resolved = typeof value === 'function' ? value(prevIdx) : value;
        const ids = new Set<string>();
        for (const i of resolved) {
          const run = runs[i];
          if (run && run.id) ids.add(run.id);
        }
        return ids;
      });
    },
    []
  );

  // Direct ID-based selector for call sites that already hold the FRESH runs array
  // (e.g. right after split / manual-add) so selection is exact and never races the
  // batched setResult. `runIds` are stable PipeRun.id values.
  const selectRunIds = useCallback((ids: string[]) => {
    setSelectedRunIds(new Set(ids.filter(Boolean)));
  }, []);

  const canvasRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const osdModuleRef = useRef<any>(null);
  const highlightOverlayRef = useRef<HTMLElement | null>(null);

  // Detection job resources (WebSocket + polling) — tracked so they can be torn
  // down on unmount or when the active sheet/project changes. Without this the
  // poll interval keeps running after navigation and the app gets progressively
  // heavier each time a project is opened.
  const pollIntervalRef = useRef<any>(null);
  const detectionWsRef = useRef<WebSocket | null>(null);

  // All pending status-toast timeouts, cleared on unmount to avoid setState leaks.
  const toastTimeoutsRef = useRef<any[]>([]);

  const showToast = useCallback((msg: string, ms: number = 2000) => {
    setStatusToast(msg);
    const t = setTimeout(() => setStatusToast(null), ms);
    toastTimeoutsRef.current.push(t);
    // Keep the tracked list bounded.
    if (toastTimeoutsRef.current.length > 40) {
      toastTimeoutsRef.current = toastTimeoutsRef.current.slice(-20);
    }
  }, []);

  const stopDetectionResources = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (detectionWsRef.current) {
      try {
        detectionWsRef.current.onmessage = null;
        detectionWsRef.current.onerror = null;
        detectionWsRef.current.close();
      } catch (e) {}
      detectionWsRef.current = null;
    }
  }, []);

  // Global teardown: stop detection resources, timers and the OSD viewer when
  // the workspace unmounts so nothing leaks across project navigations.
  useEffect(() => {
    return () => {
      stopDetectionResources();
      toastTimeoutsRef.current.forEach((t) => clearTimeout(t));
      toastTimeoutsRef.current = [];
      if (viewerRef.current) {
        try { viewerRef.current.destroy(); } catch (e) {}
        viewerRef.current = null;
      }
    };
  }, [stopDetectionResources]);

  // Push action to Undo/Redo history stack (15-20 steps)
  const pushHistory = useCallback(
    (desc: string, prevRuns: PipeRun[], nextRuns: PipeRun[], prevPids: PipingID[], nextPids: PipingID[]) => {
      setHistory((prev) => {
        const trimmed = prev.slice(0, historyIndex + 1);
        const nextHistory = [
          ...trimmed,
          { desc, prevRuns, nextRuns, prevPids, nextPids },
        ].slice(-20);
        setHistoryIndex(nextHistory.length - 1);
        return nextHistory;
      });
      setHasUnsavedChanges(true);
    },
    [historyIndex]
  );

  const handleUndo = useCallback(() => {
    if (historyIndex >= 0 && history[historyIndex]) {
      const action = history[historyIndex];
      if (result && projectId && activeSheet) {
        const restored = { ...result, runs: action.prevRuns, piping_ids: action.prevPids };
        setResult(restored);
        // Persist the restored state so the backend's runs array stays in sync
        // with what the user sees. Without this, undo/redo left the DB on a
        // different run count and later index-based edits errored out.
        patchResult(projectId, activeSheet.id, restored).catch((e) =>
          console.error('Gagal sinkronisasi undo ke server:', e)
        );
      }
      setHistoryIndex((idx) => idx - 1);
      showToast(`Undo: ${action.desc}`, 2000);
    }
  }, [historyIndex, history, result, projectId, activeSheet, showToast]);

  const handleRedo = useCallback(() => {
    if (historyIndex < history.length - 1 && history[historyIndex + 1]) {
      const action = history[historyIndex + 1];
      if (result && projectId && activeSheet) {
        const restored = { ...result, runs: action.nextRuns, piping_ids: action.nextPids };
        setResult(restored);
        patchResult(projectId, activeSheet.id, restored).catch((e) =>
          console.error('Gagal sinkronisasi redo ke server:', e)
        );
      }
      setHistoryIndex((idx) => idx + 1);
      showToast(`Redo: ${action.desc}`, 2000);
    }
  }, [historyIndex, history, result, projectId, activeSheet, showToast]);

  // Recolor selected runs in frontend state
  const handleRecolorRuns = (runIdxs: number[], newColor: string) => {
    if (!result || runIdxs.length === 0) return;
    const prevRuns = [...result.runs];
    const nextRuns = result.runs.map((r, i) =>
      runIdxs.includes(i) ? { ...r, color: newColor } : r
    );
    pushHistory(
      `Ubah warna ${runIdxs.length} pipa`,
      prevRuns,
      nextRuns,
      result.piping_ids,
      result.piping_ids
    );
    setResult({ ...result, runs: nextRuns });
    showToast(`Warna diperbarui ke ${newColor}`, 2000);
  };

  // Split selected line via backend API
  const handleSplitRun = async (runIdx: number, x: number, y: number) => {
    if (!result || !projectId || !activeSheet) return;
    try {
      const res = await splitRun(projectId, activeSheet.id, runIdx, x, y);
      if (res.result) {
        const prevRuns = [...result.runs];
        const prevPids = [...result.piping_ids];
        const nextRuns = res.result.runs;
        const nextPids = res.result.piping_ids || result.piping_ids;
        pushHistory(`Split pipa #${runIdx}`, prevRuns, nextRuns, prevPids, nextPids);
        setResult({ ...result, runs: nextRuns, piping_ids: nextPids });
        // Automatically exit split mode and deselect cleanly so user can freely click either piece
        setSplitMode(false);
        selectRunIds([]);
        showToast(`Pipa #${runIdx} berhasil dipecah menjadi 2 segmen!`, 3000);
      }
    } catch (err: any) {
      alert(err.message || 'Gagal memecah pipa');
    }
  };

  // Delete runs (single or batch) via backend API
  const handleDeleteRuns = async (runIdxs: number[]) => {
    if (!result || !projectId || !activeSheet || runIdxs.length === 0) return;
    try {
      const prevRuns = [...result.runs];
      const prevPids = [...result.piping_ids];
      let updatedResult: DigitizationResult;
      if (runIdxs.length === 1) {
        const res = await deleteRun(projectId, activeSheet.id, runIdxs[0]);
        updatedResult = res.result;
      } else {
        const res = await batchDeleteRuns(projectId, activeSheet.id, runIdxs);
        updatedResult = res.result;
      }
      pushHistory(
        `Hapus ${runIdxs.length} pipa`,
        prevRuns,
        updatedResult.runs,
        prevPids,
        updatedResult.piping_ids || prevPids
      );
      setResult(updatedResult);
      setSelectedRunIndices(new Set());
      showToast(`${runIdxs.length} pipa berhasil dihapus!`, 3000);
    } catch (err: any) {
      alert(err.message || 'Gagal menghapus pipa');
    }
  };

  // Update label / tag of a run.
  //
  // NOTE: we deliberately persist via the whole-result PATCH (`patchResult`)
  // instead of the index-based `/runs/{run_idx}/label` endpoint. Index-based
  // mutations race with local-only edits (undo/redo, manual pen, box trace) that
  // change `result.runs` in memory without round-tripping to the DB, so a
  // remembered index could point past the backend's array and raise
  // "Run index N out of range". Sending the full result keeps the backend's runs
  // array byte-for-byte in sync with what the user sees.
  const handleUpdateRunLabel = async (runIdx: number, label: string) => {
    if (!result || !projectId || !activeSheet) return;
    if (runIdx < 0 || runIdx >= (result.runs?.length || 0)) {
      showToast('Garis pipa tidak ditemukan (mungkin sudah berubah) — coba klik ulang.', 3000);
      return;
    }
    const trimmed = label.trim();

    // Duplicate-tag detection: if another line already carries this tag, ask the
    // user whether to MERGE this segment into that existing line instead of
    // blindly creating a second line with the same tag.
    const existingIdx = trimmed
      ? (result.piping_ids || []).findIndex(
          (p) => (p.pid || '').trim().toLowerCase() === trimmed.toLowerCase()
        )
      : -1;
    if (existingIdx >= 0) {
      const existing = result.piping_ids[existingIdx];
      const alreadyLinked =
        existing.run_idx === runIdx || (existing.extra_runs || []).includes(runIdx);
      if (!alreadyLinked) {
        const ok = window.confirm(
          `Tag pipa "${trimmed}" sudah dipakai oleh line lain.\n\n` +
            `OK  = Gabungkan segmen ini ke line tersebut (merge).\n` +
            `Cancel = Batalkan, supaya kamu bisa memakai tag lain.`
        );
        if (ok) {
          await handleMergeRunIntoPid(runIdx, existingIdx, trimmed);
          return;
        }
        return; // user chose to cancel — leave the tag unchanged
      }
    }

    try {
      const prevRuns = [...result.runs];
      const prevPids = [...result.piping_ids];
      const nextRuns = result.runs.map((r, i) =>
        i === runIdx ? { ...r, label: trimmed, manual: true } : r
      );
      const nextPids = (result.piping_ids || []).map((p) =>
        p.run_idx === runIdx && (p.pid || '').toLowerCase() === trimmed.toLowerCase()
          ? { ...p, pid: trimmed }
          : p
      );
      const updated = await patchResult(projectId, activeSheet.id, {
        ...result,
        runs: nextRuns,
        piping_ids: nextPids,
      });
      pushHistory(
        `Ubah tag pipa #${runIdx}`,
        prevRuns,
        updated.runs,
        prevPids,
        updated.piping_ids || prevPids
      );
      setResult(updated);
      setHasUnsavedChanges(true);
      showToast(`Tag pipa diperbarui: ${trimmed || '(dikosongkan)'}`, 2500);
    } catch (err: any) {
      alert(err.message || 'Gagal memperbarui tag pipa');
    }
  };

  // Merge a run (segment) into an existing piping ID line: attach the run to the
  // target pid (as its run_idx if it has none, otherwise as an extra run) and
  // stamp the same tag on the run so the canvas shows the combined line.
  const handleMergeRunIntoPid = async (runIdx: number, pidIdx: number, tag: string) => {
    if (!result || !projectId || !activeSheet) return;
    try {
      const prevRuns = [...result.runs];
      const prevPids = [...result.piping_ids];
      const nextRuns = result.runs.map((r, i) =>
        i === runIdx ? { ...r, label: tag, pid: tag, manual: true } : r
      );
      const nextPids = (result.piping_ids || []).map((p, i) => {
        if (i !== pidIdx) return p;
        const p2 = { ...p };
        if (p2.run_idx == null || p2.run_idx < 0) {
          p2.run_idx = runIdx;
          p2.state = 'attached';
        } else {
          const extra = new Set(p2.extra_runs || []);
          extra.add(runIdx);
          p2.extra_runs = Array.from(extra).filter((e) => e !== p2.run_idx);
        }
        return p2;
      });
      const updated = await patchResult(projectId, activeSheet.id, {
        ...result,
        runs: nextRuns,
        piping_ids: nextPids,
      });
      pushHistory(
        `Gabung pipa #${runIdx} ke line "${tag}"`,
        prevRuns,
        updated.runs,
        prevPids,
        updated.piping_ids || prevPids
      );
      setResult(updated);
      setHasUnsavedChanges(true);
      showToast(`Pipa #${runIdx} digabungkan ke line "${tag}"`, 3000);
    } catch (err: any) {
      alert(err.message || 'Gagal menggabungkan pipa');
    }
  };

  // Select a run from the inspector, set active state, and pan/zoom canvas to its bounding box
  const handleSelectRun = (run: PipeRun, idx: number) => {
    setSelectedRunIndices(new Set([idx]));
    setEditingRunLabel(run.label || run.pid || '');
    if (run.points && run.points.length > 0) {
      const xs = run.points.map((p) => p[0]);
      const ys = run.points.map((p) => p[1]);
      zoomToBbox(
        Math.min(...xs) - 60,
        Math.min(...ys) - 60,
        Math.max(...xs) + 60,
        Math.max(...ys) + 60
      );
    } else if (run.x1 != null && run.y1 != null) {
      zoomToBbox(run.x1 - 60, run.y1 - 60, run.x2 + 60, run.y2 + 60);
    }
  };

  // Synchronize canvas selection to sidebar scrolling
  useEffect(() => {
    if (selectedRunIndices.size === 1) {
      const idx = Array.from(selectedRunIndices)[0];
      const run = result?.runs?.[idx];
      if (run) {
        setEditingRunLabel(run.label || run.pid || '');
      }
      if (digitizeSubTab === 'pipes' && runRowRefs.current[idx]) {
        runRowRefs.current[idx]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
  }, [selectedRunIndices, result?.runs, digitizeSubTab]);

  // With ID-based selection the derived index set can never point at a stale /
  // re-indexed run (a missing id simply drops out). The only thing left to keep in
  // sync is split mode: it must turn itself off when the selection empties.
  useEffect(() => {
    if (selectedRunIds.size > 0 && selectedRunIndices.size === 0) {
      setSelectedRunIds(new Set());
      setSplitMode(false);
    } else if (selectedRunIndices.size === 0 && splitMode) {
      setSplitMode(false);
    }
  }, [selectedRunIds, selectedRunIndices, splitMode]);

  // The line selection / split mode only makes sense in DIGITIZE mode. When the
  // user switches to Corrosion System / Circuit / Topology / Report (or re-opens
  // the digitizer on another sheet), drop the selection so the orange selection
  // halo can never linger on the canvas after the user navigates away.
  useEffect(() => {
    setSelectedRunIndices(new Set());
    setSplitMode(false);
  }, [mode, activeSheet?.id, setSelectedRunIndices]);

  // Persist all manual changes to database
  const handleSaveAllChanges = useCallback(async () => {
    if (!result || !projectId || !activeSheet || savingChanges) return;
    setSavingChanges(true);
    try {
      await patchResult(projectId, activeSheet.id, result);
      setHasUnsavedChanges(false);
      showToast('Semua perubahan pipa berhasil disimpan ke database!', 3000);
    } catch (err: any) {
      alert('Gagal menyimpan perubahan: ' + (err.message || 'Server error'));
    } finally {
      setSavingChanges(false);
    }
  }, [result, projectId, activeSheet, savingChanges, showToast]);

  const handleManualRun = useCallback(async (points: [number, number][]) => {
    if (!result || !projectId || !activeSheet || points.length < 2) return;
    const now = Date.now();
    const nextRun: PipeRun = {
      id: `manual-run-${now}`,
      points,
      axis: points.length === 2 ? 'd' : 'poly',
      x1: points[0][0], y1: points[0][1],
      x2: points[points.length - 1][0], y2: points[points.length - 1][1],
      color: '#2563EB',
      manual: true,
    };
    const prevRuns = [...result.runs];
    const nextRuns = [...result.runs, nextRun];
    pushHistory('Tambah pipa manual', prevRuns, nextRuns, result.piping_ids, result.piping_ids);
    const updated = await patchResult(projectId, activeSheet.id, { ...result, runs: nextRuns });
    setResult(updated);
    const added = updated.runs[updated.runs.length - 1];
    selectRunIds(added && added.id ? [added.id] : []);
    setTraceTool('pan');
  }, [result, projectId, activeSheet, pushHistory, selectRunIds]);

  // Update vertex points of a pipe run (after draggable control points adjusted)
  const handleUpdateRunPoints = useCallback(
    async (runIdx: number, points: [number, number][]) => {
      if (!result || !projectId || !activeSheet || points.length < 2) return;
      try {
        const prevRuns = [...result.runs];
        const prevPids = [...result.piping_ids];
        const res = await updateRunPoints(projectId, activeSheet.id, runIdx, points);
        const nextRuns = res.result.runs;
        pushHistory(
          `Luruskan / Edit titik pipa #${runIdx}`,
          prevRuns,
          nextRuns,
          prevPids,
          res.result.piping_ids || prevPids
        );
        setResult(res.result);
        setHasUnsavedChanges(true);
        showToast(`Titik koordinat pipa #${runIdx} berhasil disesuaikan!`, 2500);
      } catch (err: any) {
        alert('Gagal mengupdate titik pipa: ' + (err.message || 'Server error'));
      }
    },
    [result, projectId, activeSheet, pushHistory, showToast]
  );

  const handleRescan = useCallback(
    async (bounds: { x1: number; y1: number; x2: number; y2: number }) => {
      if (!projectId || !activeSheet || !result) return;
      try {
        const data = await traceRegion(projectId, activeSheet.id, bounds);
        const nextRuns = data.result?.runs || [...result.runs, ...(data.new_runs || [])];
        const stitched = data.stitched_runs_count ?? data.stitched ?? 0;
        pushHistory(
          'Re-scan area pipa',
          result.runs,
          nextRuns,
          result.piping_ids,
          data.result?.piping_ids || result.piping_ids
        );
        setResult(data.result || { ...result, runs: nextRuns });
        setHasUnsavedChanges(true);
        const added = data.new_runs?.length || 0;
        showToast(
          stitched > 0
            ? `Re-scan selesai! ${stitched} pipa tersambung otomatis${added > 0 ? `, ${added} pipa baru` : ''}`
            : `Re-scan selesai! Menambahkan ${added} pipa baru`,
          3000
        );
        setTraceTool('pan');
      } catch (err: any) {
        alert('Gagal melakukan re-scan area: ' + (err.message || 'Server error'));
      }
    },
    [projectId, activeSheet, result, pushHistory, showToast]
  );

  // Keyboard shortcuts listener: Ctrl+Z (Undo), Ctrl+Y (Redo), Ctrl+S (Save), Esc (Cancel)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // GUARD: never hijack keys while the user is typing inside a form field.
      // Without this, pressing Backspace/Delete while renaming a pipe tag in the
      // action popover would delete the whole pipe instead of a character.
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.tagName === 'SELECT' ||
          t.isContentEditable)
      ) {
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        if (e.shiftKey) {
          e.preventDefault();
          handleRedo();
        } else {
          e.preventDefault();
          handleUndo();
        }
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        handleRedo();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        handleSaveAllChanges();
      } else if (e.key === 'Escape') {
        setSplitMode(false);
        setTraceTool('pan');
        setSelectedRunIndices(new Set());
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedRunIndices.size > 0) {
        e.preventDefault();
        handleDeleteRuns(Array.from(selectedRunIndices));
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleUndo, handleRedo, handleSaveAllChanges, selectedRunIndices, handleDeleteRuns, setSelectedRunIndices]);

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

  // ---------------------------------------------------------------------------
  // RESUME in-flight detection after navigation/remount.
  //
  // The job id is NOT persisted client-side, so when the user presses Back and
  // re-opens the same sheet, the page mounts fresh with blank detection state.
  // Previously this left the user staring at a blank canvas with no spinner.
  //
  // Fix: derive state from the SERVER. If the sheet's persisted status is
  // 'detecting', re-attach to its most recent job and show the SAME loading UI.
  // This only polls WHILE the sheet is detecting; once it resolves (or on
  // unmount) all resources are torn down, so idle pages cost nothing.
  // ---------------------------------------------------------------------------
  const resumedJobRef = useRef<string | null>(null);
  useEffect(() => {
    if (!projectId || !activeSheet) return;
    if (activeSheet.status !== 'detecting') return;

    // Already attached to this exact sheet/job (e.g. we started it ourselves via
    // handleRunDetection) — don't attach twice.
    if (resumedJobRef.current === activeSheet.id) return;
    resumedJobRef.current = activeSheet.id;

    let cancelled = false;

    setDetecting(true);
    setProgressMsg((prev) => prev || 'Detection in progress — resuming status…');

    const onCompleted = () => {
      if (cancelled) return;
      stopDetectionResources();
      setDetecting(false);
      setProgressPct(100);
      setProgressMsg('Digitasi & Sistemisasi selesai.');
      resumedJobRef.current = null;
      fetchProject(projectId).then((p) => {
        setProject(p);
        const updated = p.sheets?.find((sh) => sh.id === activeSheet.id);
        if (updated) setActiveSheet(updated);
      });
      fetchResult(projectId, activeSheet.id).then(setResult).catch(console.error);
      fetchSystems(projectId, activeSheet.id).then(setSystems).catch(console.error);
      fetchValidation(projectId, activeSheet.id).then(setValidation).catch(console.error);
    };

    const onFailed = (msg?: string) => {
      if (cancelled) return;
      stopDetectionResources();
      setDetecting(false);
      resumedJobRef.current = null;
      if (msg) setProgressMsg(msg);
    };

    const attach = async () => {
      let jobId = activeSheet.latest_job_id ?? null;
      if (!jobId) {
        try {
          const job = await fetchLatestJobForSheet(activeSheet.id);
          jobId = job?.job_id ?? null;
        } catch (e) {
          /* fall through to polling below */
        }
      }
      if (cancelled) return;

      // Apply any already-known progress immediately.
      if (jobId) {
        fetchJob(jobId)
          .then((j) => {
            if (cancelled) return;
            if (j.progress_pct !== undefined) setProgressPct(j.progress_pct);
            if (j.message) setProgressMsg(j.message);
            if (j.status === 'failed') onFailed(j.message || j.error || 'Detection failed');
          })
          .catch(() => {});

        const wsUrl =
          (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000') + `/ws/progress/${jobId}`;
        try {
          const ws = new WebSocket(wsUrl);
          detectionWsRef.current = ws;
          ws.onmessage = (event) => {
            try {
              const data = JSON.parse(event.data);
              if (data.pct !== undefined) setProgressPct(data.pct);
              if (data.message) setProgressMsg(data.message);
              if (data.step === 'completed') onCompleted();
              else if (data.step === 'failed') onFailed(data.message || 'Detection failed');
            } catch (err) {}
          };
          ws.onerror = () => {};
        } catch (e) {
          /* polling fallback handles it */
        }
      }

      // Lightweight fallback: poll only while the sheet is still 'detecting'.
      // Max ~10 minutes (200 * 3s) then give up to avoid an endless timer.
      let ticks = 0;
      pollIntervalRef.current = setInterval(async () => {
        ticks += 1;
        if (ticks > 200) {
          stopDetectionResources();
          return;
        }
        try {
          if (jobId) {
            const j = await fetchJob(jobId);
            if (j.progress_pct !== undefined) setProgressPct(j.progress_pct);
            if (j.message) setProgressMsg(j.message);
            if (j.status === 'completed') return onCompleted();
            if (j.status === 'failed') return onFailed(j.message || j.error || 'Detection failed');
          }
          const p = await fetchProject(projectId);
          const s = p.sheets?.find((sh) => sh.id === activeSheet.id);
          if (s && s.status === 'detected') onCompleted();
          else if (s && s.status === 'error') onFailed('Detection failed.');
        } catch (e) {}
      }, 3000);
    };

    attach();

    return () => {
      cancelled = true;
      stopDetectionResources();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, activeSheet?.id, activeSheet?.status, activeSheet?.latest_job_id]);

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

      // The base image is ALWAYS the raw CAD drawing. Circuit/system coloring is
      // rendered as a vector layer by InteractivePipeCanvas (see colorOverrideMap),
      // so we never swap the OSD tile source on mode change. This keeps the SVG
      // tracing overlay attached and avoids re-downloading/re-rendering a full-res PNG.
      const initialUrl = getRawImageUrl(projectId, activeSheet.id);

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
      setViewerReady(true);
    });

    return () => {
      isCancelled = true;
      if (viewerRef.current) {
        viewerRef.current.destroy();
        viewerRef.current = null;
      }
      setViewerReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, activeSheet]);

  // Keep the base image pinned to the raw CAD drawing. Circuit/system coloring is a
  // vector overlay, so this effect only needs to react to sheet changes — NOT to
  // mode/showOverlay, which previously forced a full-res `viewer.open()` that wiped
  // the tracing overlay on every tab switch.
  const isDetected = activeSheet?.status === 'detected' || Boolean(result);

  // Show the detection progress UI whenever EITHER we locally started a job OR
  // the persisted sheet status says a job is running. The persisted-status branch
  // is what guarantees a spinner is shown after a Back-and-reopen remount (the
  // local `detecting` flag is lost on navigation, but server `status` is not).
  // The top progress bar always shows when detecting; the full-page overlay
  // only shows on the first detection (when no result exists yet).
  const showDetectionProgress =
    detecting || activeSheet?.status === 'detecting';
  useEffect(() => {
    if (!viewerRef.current || !activeSheet || !projectId) return;
    const viewer = viewerRef.current;
    if (!viewer.viewport) return;

    const targetUrl = getRawImageUrl(projectId, activeSheet.id);

    const currentItem = viewer.world?.getItemAt(0);
    const currentSource = currentItem?.source?.url || currentItem?._url;
    if (currentSource === targetUrl) return;

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
  }, [projectId, activeSheet]);

  // Client-side color override for Corrosion System / Circuit views.
  // Maps run index -> CSS rgb() color so circuits are rendered as a vector layer
  // on the raw CAD image (no server-rendered marked PNG needed).
  const colorOverrideMap = useMemo<Map<number, string> | null>(() => {
    if (mode !== 'system' && mode !== 'circuit') return null;
    if (!systems || systems.length === 0) return null;

    const map = new Map<number, string>();
    const rgb = (c: number[]) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;

    if (mode === 'system') {
      for (const sys of systems) {
        const color = rgb(sys.color);
        for (const idx of sys.run_idxs || []) map.set(idx, color);
      }
    } else {
      for (const sys of systems) {
        for (const circ of sys.circuits || []) {
          const color = rgb(circ.color);
          for (const idx of circ.run_idxs || []) map.set(idx, color);
        }
      }
    }
    return map;
  }, [mode, systems]);

  // Map the active view mode to a backend export mode so the exported
  // PDF/PNG reflects exactly what the user is looking at:
  //   digitize -> engineer (per-run pipe coloring)
  //   system   -> corrosion system coloring
  //   circuit  -> corrosion circuit coloring
  // Topology/Report have no raster counterpart, so they fall back to engineer.
  const exportMode: 'engineer' | 'system' | 'circuit' =
    mode === 'system' ? 'system' : mode === 'circuit' ? 'circuit' : 'engineer';

  // Remove the purple "focused target" highlight overlay from the OpenSeadragon
  // viewer. This must be called on its own (not only inside zoomToBbox) so the
  // highlight can be cleared when the user navigates away — otherwise the box
  // keeps pulsing over the canvas after switching System/Circuit tabs.
  const clearHighlight = useCallback(() => {
    const viewer = viewerRef.current;
    if (viewer && highlightOverlayRef.current) {
      try { viewer.removeOverlay(highlightOverlayRef.current); } catch (e) {}
    }
    highlightOverlayRef.current = null;
  }, []);

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
        clearHighlight();
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
        highlightOverlayRef.current = highlightEl;
      }
    } catch (e) {
      console.error('Failed to zoom to bbox:', e);
    }
  };

  // Clear the purple target highlight whenever the user navigates away from the
  // item that created it: switching view mode (System ⇄ Circuit ⇄ Digitize) or
  // switching sheets. These transitions never co-occur with a fresh zoomToBbox,
  // so it's safe to clear here without racing the highlight that a new selection
  // just installed. Without this the pulsing box lingers on the canvas after the
  // user moves on to another tab/list item.
  useEffect(() => {
    clearHighlight();
  }, [mode, activeSheet?.id, clearHighlight]);

  // Clear the purple focus highlight when the user clicks empty canvas space.
  // Clicks that land on an interactive SVG element (pipe hit-target, vertex,
  // drawing tool, popover) are ignored so this never fights the tracing tools.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onCanvasClick = (ev: MouseEvent) => {
      const target = ev.target as HTMLElement | null;
      if (!target) return;
      // Anything inside the interactive pipe overlay (or a tool/popover) keeps its state.
      if (target.closest('[data-pipe-interactive], .osd-pipe-overlay, svg, button, [role="dialog"]')) {
        return;
      }
      clearHighlight();
      setSelectedSystemIdx(null);
      setSelectedCircuitCode(null);
      setSelectedPidIdx(null);
    };
    el.addEventListener('click', onCanvasClick);
    return () => el.removeEventListener('click', onCanvasClick);
  }, [clearHighlight]);

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
    // Toggle: clicking the same system again deselects it and clears the purple
    // focus highlight, so the marker never gets stuck on the canvas.
    if (selectedSystemIdx === idx) {
      setSelectedSystemIdx(null);
      clearHighlight();
      return;
    }
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
    // Toggle: clicking the same circuit again deselects it and clears the highlight.
    if (selectedCircuitCode === c.code) {
      setSelectedCircuitCode(null);
      clearHighlight();
      return;
    }
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

  // Trigger Detection Pipeline (fast "lines_only" mode or "full" mode)
  const handleRunDetection = async (detectionMode: 'full' | 'lines_only' = 'full') => {
    if (!projectId || !activeSheet) return;
    try {
      // Ensure any previous job's resources are torn down first.
      stopDetectionResources();

      setDetecting(true);
      setProgressPct(0);
      setProgressMsg(
        detectionMode === 'lines_only'
          ? 'Tracing jalur pipa (Fast Line Only)...'
          : 'Initializing P&ID pipeline...'
      );

      const job = await triggerDetection(projectId, activeSheet.id, undefined, undefined, detectionMode);

      // Mark this sheet as locally-attached so the resume effect below does not
      // re-attach to the same in-flight job (it would otherwise create a second
      // WebSocket + poll interval for a job we already own).
      resumedJobRef.current = activeSheet.id;

      // WebSocket connection for live progress
      const wsUrl = (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000') + `/ws/progress/${job.job_id}`;
      const ws = new WebSocket(wsUrl);
      detectionWsRef.current = ws;

      let completedHandled = false;

      const onCompleted = () => {
        if (completedHandled) return;
        completedHandled = true;
        stopDetectionResources();
        setDetecting(false);
        setProgressPct(100);
        setProgressMsg(
          detectionMode === 'lines_only'
            ? 'Tracing garis selesai.'
            : 'Digitasi & Sistemisasi selesai.'
        );
        resumedJobRef.current = null;

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

      // Periodic polling fallback in case WebSocket drops or times out.
      // Tracked in a ref so it is cleared on unmount / sheet change.
      pollIntervalRef.current = setInterval(async () => {
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
          if (data.step === 'completed' || data.pct === 100) {
            onCompleted();
          } else if (data.step === 'failed') {
            stopDetectionResources();
            setDetecting(false);
            alert('Detection error: ' + data.message);
          }
        } catch (err) {}
      };

      ws.onerror = () => {
        // WebSocket error, fallback polling continues to monitor progress
      };
    } catch (err: any) {
      stopDetectionResources();
      setDetecting(false);
      resumedJobRef.current = null;
      alert('Failed to trigger detection: ' + (err.message || err));
    }
  };

  // Trigger On-Demand AI Enrichment (OCR line numbers & YOLO symbols) on existing runs
  const handleRunEnrichment = async () => {
    if (!projectId || !activeSheet) return;
    try {
      stopDetectionResources();

      setDetecting(true);
      setProgressPct(0);
      setProgressMsg('Memulai pindai AI OCR & Simbol (Enrich)...');

      const job = await triggerEnrichment(projectId, activeSheet.id);
      resumedJobRef.current = activeSheet.id;

      const wsUrl = (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000') + `/ws/progress/${job.job_id}`;
      const ws = new WebSocket(wsUrl);
      detectionWsRef.current = ws;

      let completedHandled = false;

      const onCompleted = () => {
        if (completedHandled) return;
        completedHandled = true;
        stopDetectionResources();
        setDetecting(false);
        setProgressPct(100);
        setProgressMsg('Pindai simbol & teks selesai.');
        resumedJobRef.current = null;

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

      pollIntervalRef.current = setInterval(async () => {
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
          if (data.step === 'completed' || data.pct === 100) {
            onCompleted();
          } else if (data.step === 'failed') {
            stopDetectionResources();
            setDetecting(false);
            alert('Enrichment error: ' + data.message);
          }
        } catch (err) {}
      };

      ws.onerror = () => {};
    } catch (err: any) {
      stopDetectionResources();
      setDetecting(false);
      resumedJobRef.current = null;
      alert('Failed to trigger enrichment: ' + (err.message || err));
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
      <header className="bg-white border-b border-slate-200 px-3 lg:px-6 py-2.5 flex flex-wrap items-center justify-between gap-2 gap-y-2 shadow-sm z-20">
        <div className="flex items-center space-x-2 lg:space-x-4 min-w-0">
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
        <div className="flex bg-slate-100 p-1 rounded-xl border border-slate-200 overflow-x-auto max-w-full order-3 lg:order-none">
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

        {/* Enrich Button (On-Demand AI) right next to Digitize / System mode switcher */}
        {activeSheet && (
          <button
            onClick={handleRunEnrichment}
            disabled={showDetectionProgress}
            className={`px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition border ${
              showDetectionProgress
                ? 'bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed'
                : 'bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-700 hover:to-indigo-700 text-white shadow-sm hover:shadow'
            }`}
            title="Pindai OCR line number dan simbol YOLO pada sheet yang sudah di-trace"
          >
            <Sparkles className="w-3.5 h-3.5 fill-current text-amber-300" />
            <span className="hidden sm:inline"> Pindai Simbol & Teks (Enrich)</span>
            <span className="sm:hidden"> Enrich</span>
          </button>
        )}

        {/* Action Controls */}
        <div className="flex items-center space-x-2">
          {/* Import Line List Button */}
          <button
            onClick={() => setShowLineListModal(true)}
            className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition"
            title="Import Line List Excel / CSV"
          >
            <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-600" />
            <span className="hidden xl:inline">Line List</span>
          </button>

          {activeSheet && (
            <div className="relative inline-block text-left" ref={detectDropdownRef}>
              <button
                onClick={() => setDetectDropdownOpen(!detectDropdownOpen)}
                disabled={showDetectionProgress}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow transition ${
                  showDetectionProgress
                    ? 'bg-slate-300 text-slate-500 cursor-not-allowed'
                    : 'bg-indigo-600 hover:bg-indigo-700 text-white'
                }`}
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span className="hidden xl:inline">{showDetectionProgress ? 'Detecting...' : 'Detect P&ID'}</span>
                <ChevronDown className="w-3.5 h-3.5 opacity-80" />
              </button>

              {detectDropdownOpen && !showDetectionProgress && (
                <div className="absolute right-0 mt-1.5 w-64 rounded-xl bg-white shadow-xl border border-slate-200 z-50 py-1.5 text-xs animate-in fade-in slide-in-from-top-1 duration-150">
                  <div className="px-3 py-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Pilih Mode Deteksi
                  </div>
                  <button
                    onClick={() => {
                      setDetectDropdownOpen(false);
                      handleRunDetection('lines_only');
                    }}
                    className="w-full text-left px-3 py-2 hover:bg-amber-50/80 text-slate-800 flex items-start space-x-2.5 transition"
                  >
                    <Zap className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                    <div>
                      <div className="font-semibold text-slate-900 flex items-center gap-1.5">
                        <span> Trace Lines Only</span>
                        <span className="bg-amber-100 text-amber-800 text-[10px] font-bold px-1.5 py-0.5 rounded">Cepat &lt;5s</span>
                      </div>
                      <div className="text-[11px] text-slate-500 mt-0.5">Ekstrak garis pipa saja. Lewati OCR & YOLO.</div>
                    </div>
                  </button>
                  <div className="h-px bg-slate-100 my-1" />
                  <button
                    onClick={() => {
                      setDetectDropdownOpen(false);
                      handleRunDetection('full');
                    }}
                    className="w-full text-left px-3 py-2 hover:bg-indigo-50/80 text-slate-800 flex items-start space-x-2.5 transition"
                  >
                    <Bot className="w-4 h-4 text-indigo-600 mt-0.5 shrink-0" />
                    <div>
                      <div className="font-semibold text-slate-900 flex items-center gap-1.5">
                        <span> Trace Full</span>
                        <span className="bg-indigo-100 text-indigo-800 text-[10px] font-bold px-1.5 py-0.5 rounded">AI & OCR</span>
                      </div>
                      <div className="text-[11px] text-slate-500 mt-0.5">Lengkap: Tracing pipa + OCR 3-angle + Simbol YOLO.</div>
                    </div>
                  </button>
                </div>
              )}
            </div>
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
              <span className="hidden 2xl:inline">{showOverlay ? 'Circuit Overlay: ON' : 'Circuit Overlay: OFF'}</span>
            </button>
          )}

          {/* Export Button (opens modal) */}
          {(result || activeSheet?.status === 'detected') && (
            <button
              onClick={() => setShowExportModal(true)}
              className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition"
              title="Export hasil digitization / corrosion"
            >
              <Download className="w-3.5 h-3.5" />
              <span className="hidden xl:inline">Export</span>
            </button>
          )}
        </div>
      </header>

      {/* Progress Bar (during detection) */}
      {showDetectionProgress && (
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

          {/* Detection-in-progress canvas overlay: guarantees a visible spinner +
              explanation on the canvas itself, so a remount mid-detection never
              shows a blank-looking image with no feedback. */}
          {showDetectionProgress && !result && (
            <div className="absolute inset-0 z-30 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm pointer-events-none">
              <div className="bg-white/95 border border-slate-200 rounded-2xl shadow-2xl px-8 py-6 flex flex-col items-center space-y-3 max-w-sm text-center">
                <RefreshCw className="w-8 h-8 animate-spin text-indigo-600" />
                <div className="text-sm font-bold text-slate-800">
                  Deteksi P&ID sedang berjalan…
                </div>
                <div className="text-xs text-slate-600 leading-relaxed">
                  {progressMsg ||
                    'Sistem sedang membaca gambar, mendeteksi simbol, dan melacak garis pipa. Progres tetap berjalan di server walau Anda berpindah halaman.'}
                </div>
                <div className="w-full flex items-center space-x-2">
                  <div className="flex-1 h-2 bg-indigo-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-indigo-600 transition-all duration-300"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                  <span className="text-xs font-bold text-indigo-700 w-9 text-right">
                    {progressPct}%
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Interactive Pipe Canvas Tooling (Phase B.5) */}
          {result && activeSheet && (
            <InteractivePipeCanvas
              viewer={viewerReady ? viewerRef.current : null}
              osdModule={osdModuleRef.current}
              width={result.w || activeSheet.width || 3000}
              height={result.h || activeSheet.height || 2000}
              runs={result.runs || []}
              pipingIds={result.piping_ids || []}
              showOverlay={showOverlay}
              opacity={traceOpacity}
              selectedRunIndices={selectedRunIndices}
              onSelectRunIndices={setSelectedRunIndices}
              onRecolorRuns={handleRecolorRuns}
              onSplitRun={handleSplitRun}
              onDeleteRuns={handleDeleteRuns}
              onUpdateRunLabel={handleUpdateRunLabel}
              onUpdateRunPoints={handleUpdateRunPoints}
              splitMode={splitMode}
              onSetSplitMode={setSplitMode}
              canUndo={historyIndex >= 0}
              canRedo={historyIndex < history.length - 1}
              onUndo={handleUndo}
               onRedo={handleRedo}
               traceTool={traceTool}
               onSetTraceTool={setTraceTool}
               onRescan={handleRescan}
               onManualRun={handleManualRun}
               colorOverrideMap={colorOverrideMap}
               dimUncolored={mode === 'system' || mode === 'circuit'}
             />
          )}

          {/* Floating Canvas Controls */}
          <div className="absolute bottom-6 left-6 flex flex-wrap items-center bg-white/95 backdrop-blur border border-slate-300 rounded-xl shadow-lg p-1.5 space-x-1.5 z-40 max-w-[calc(100%-3rem)]">
            {/* Zoom / Viewport controls */}
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
                <div className="w-[1px] h-6 bg-slate-200 self-center my-auto mx-0.5" />

                {/* Visibility Toggle Button */}
                <button
                  onClick={() => setShowOverlay(!showOverlay)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition ${
                    showOverlay
                      ? 'bg-indigo-600 text-white shadow-sm hover:bg-indigo-700'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                  title="Tampilkan / Sembunyikan Garis Pipa (Show/Hide)"
                >
                  {showOverlay ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                  <span>{showOverlay ? 'Pipa: ON' : 'Pipa: OFF'}</span>
                </button>

                {/* Opacity Slider */}
                {showOverlay && (
                  <div className="flex items-center space-x-1 px-1.5 text-xs text-slate-600 border-l border-slate-200 pl-2">
                    <Sliders className="w-3 h-3 text-slate-400" />
                    <input
                      type="range"
                      min="0.1"
                      max="1.0"
                      step="0.05"
                      value={traceOpacity}
                      onChange={(e) => setTraceOpacity(parseFloat(e.target.value))}
                      className="w-16 h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                      title={`Transparansi Pipa: ${Math.round(traceOpacity * 100)}%`}
                    />
                    <span className="text-[10px] font-mono text-slate-500 w-7">
                      {Math.round(traceOpacity * 100)}%
                    </span>
                  </div>
                )}

                <div className="w-[1px] h-6 bg-slate-200 self-center my-auto mx-0.5" />

                {/* Undo & Redo buttons */}
                <button
                  onClick={handleUndo}
                  disabled={historyIndex < 0}
                  className={`p-2 rounded-lg transition ${
                    historyIndex >= 0
                      ? 'text-slate-700 hover:bg-slate-100'
                      : 'text-slate-300 cursor-not-allowed'
                  }`}
                  title="Undo aksi terakhir (Ctrl+Z)"
                >
                  <RotateCcw className="w-4 h-4" />
                </button>
                <button
                  onClick={handleRedo}
                  disabled={historyIndex >= history.length - 1}
                  className={`p-2 rounded-lg transition ${
                    historyIndex < history.length - 1
                      ? 'text-slate-700 hover:bg-slate-100'
                      : 'text-slate-300 cursor-not-allowed'
                  }`}
                  title="Redo aksi (Ctrl+Y)"
                >
                  <RotateCw className="w-4 h-4" />
                </button>

                {/* Save Changes Button */}
                {hasUnsavedChanges && (
                  <>
                    <div className="w-[1px] h-6 bg-slate-200 self-center my-auto mx-0.5" />
                    <button
                      onClick={handleSaveAllChanges}
                      disabled={savingChanges}
                      className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition animate-pulse"
                      title="Simpan Perubahan ke Database (Ctrl+S)"
                    >
                      {savingChanges ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Save className="w-3.5 h-3.5" />
                      )}
                      <span>Simpan Perubahan</span>
                    </button>
                  </>
                )}
              </>
            )}
          </div>

          {/* Temporary Status Toast */}
          {statusToast && (
            <div className="absolute bottom-36 2xl:bottom-20 left-6 z-50 bg-slate-900/90 backdrop-blur text-white text-xs font-medium px-3.5 py-2 rounded-xl shadow-xl flex items-center space-x-2 animate-in fade-in slide-in-from-bottom-2 duration-150">
              <Check className="w-4 h-4 text-emerald-400" />
              <span>{statusToast}</span>
            </div>
          )}

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
        <div className="w-80 xl:w-96 shrink-0 bg-white border-l border-slate-200 flex flex-col shadow-xl z-10">
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
                    onClick={() => setDigitizeSubTab('pipes')}
                    className={`flex-1 py-1 px-2 rounded-md transition text-center ${
                      digitizeSubTab === 'pipes'
                        ? 'bg-white text-indigo-700 shadow-sm font-bold'
                        : 'hover:text-slate-900'
                    }`}
                  >
                    Pipa ({result.runs?.length || 0})
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

                {/* Sub-tab 2: Pipe Inspector (Runs) */}
                {digitizeSubTab === 'pipes' && (
                  <div className="flex flex-col h-full overflow-hidden">
                    {/* Batch Actions & Selection Bar */}
                    <div className="p-3 bg-slate-50 border-b border-slate-200 shrink-0 space-y-2">
                      <div className="flex items-center justify-between">
                        <button
                          onClick={() => {
                            if (selectedRunIndices.size === (result.runs?.length || 0)) {
                              setSelectedRunIndices(new Set());
                            } else {
                              setSelectedRunIndices(new Set(result.runs.map((_, i) => i)));
                            }
                          }}
                          className="flex items-center space-x-1.5 text-xs font-semibold text-slate-700 hover:text-indigo-600 transition"
                        >
                          {selectedRunIndices.size === (result.runs?.length || 0) && (result.runs?.length || 0) > 0 ? (
                            <CheckSquare className="w-4 h-4 text-indigo-600" />
                          ) : (
                            <Square className="w-4 h-4 text-slate-400" />
                          )}
                          <span>
                            {selectedRunIndices.size > 0
                              ? `${selectedRunIndices.size} pipa terpilih`
                              : 'Pilih Semua'}
                          </span>
                        </button>

                        {selectedRunIndices.size > 0 && (
                          <div className="flex items-center space-x-1">
                            <button
                              onClick={() => {
                                if (confirm(`Hapus ${selectedRunIndices.size} pipa terpilih?`)) {
                                  handleDeleteRuns(Array.from(selectedRunIndices));
                                }
                              }}
                              className="px-2 py-1 bg-rose-50 hover:bg-rose-100 text-rose-700 rounded-md text-[11px] font-bold flex items-center space-x-1 transition border border-rose-200"
                              title="Hapus semua pipa terpilih"
                            >
                              <Trash2 className="w-3.5 h-3.5 text-rose-600" />
                              <span>Hapus ({selectedRunIndices.size})</span>
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Batch Color Swatches (Quick Recolor) */}
                      {selectedRunIndices.size > 1 && (
                        <div className="flex items-center justify-between pt-1 border-t border-slate-200/60 text-xs">
                          <span className="text-[11px] font-medium text-slate-500">Warna Batch:</span>
                          <div className="flex items-center space-x-1">
                            {['#2563EB', '#10B981', '#EF4444', '#F59E0B', '#8B5CF6'].map((c) => (
                              <button
                                key={c}
                                onClick={() => handleRecolorRuns(Array.from(selectedRunIndices), c)}
                                className="w-4 h-4 rounded-full border border-white shadow-sm hover:scale-125 transition"
                                style={{ backgroundColor: c }}
                                title={`Ubah warna batch ke ${c}`}
                              />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Single Pipe Inspector Property Form (when exactly 1 run selected) */}
                    {selectedRunIndices.size === 1 && (() => {
                      const activeIdx = Array.from(selectedRunIndices)[0];
                      const activeRun = result.runs[activeIdx];
                      if (!activeRun) return null;
                      return (
                        <div className="p-3 bg-indigo-50/50 border-b border-indigo-100 shrink-0 space-y-2 text-xs">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center space-x-1.5">
                              <span
                                className="w-3.5 h-3.5 rounded-full shadow-sm shrink-0 border border-white"
                                style={{ backgroundColor: activeRun.color || '#2563EB' }}
                              />
                              <span className="font-black text-slate-900 font-mono">
                                {activeRun.id || `run-${activeIdx}`}
                              </span>
                              <span className="text-[10px] uppercase font-bold px-1.5 py-0.2 rounded bg-indigo-100 text-indigo-700">
                                {activeRun.axis || 'poly'}
                              </span>
                            </div>
                            <button
                              onClick={() => {
                                if (confirm(`Hapus pipa ${activeRun.id || `run-${activeIdx}`}?`)) {
                                  handleDeleteRuns([activeIdx]);
                                }
                              }}
                              className="text-slate-400 hover:text-rose-600 transition p-1 rounded hover:bg-rose-50"
                              title="Hapus pipa ini"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>

                          {/* Rename Tag Input */}
                          <div className="space-y-1">
                            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1">
                              <Tag className="w-3 h-3 text-slate-400" />
                              <span>Label / Tag Pipa</span>
                            </label>
                            <div className="flex items-center space-x-1.5">
                              <input
                                type="text"
                                value={editingRunLabel}
                                onChange={(e) => setEditingRunLabel(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    handleUpdateRunLabel(activeIdx, editingRunLabel);
                                  }
                                }}
                                placeholder="Contoh: 605-4-GF-CCB-101"
                                className="flex-1 px-2.5 py-1 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                              />
                              <button
                                onClick={() => handleUpdateRunLabel(activeIdx, editingRunLabel)}
                                className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg text-xs transition"
                              >
                                Simpan
                              </button>
                            </div>
                          </div>

                          {/* Quick Color Palette */}
                          <div className="space-y-1 pt-1">
                            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1">
                              <Palette className="w-3 h-3 text-slate-400" />
                              <span>Warna Pipa</span>
                            </label>
                            <div className="flex items-center justify-between">
                              <div className="flex items-center space-x-1.5">
                                {['#2563EB', '#10B981', '#EF4444', '#F59E0B', '#8B5CF6'].map((c) => (
                                  <button
                                    key={c}
                                    onClick={() => handleRecolorRuns([activeIdx], c)}
                                    className={`w-5 h-5 rounded-full border-2 transition ${
                                      activeRun.color === c ? 'border-indigo-600 scale-110 shadow-md' : 'border-white hover:scale-110'
                                    }`}
                                    style={{ backgroundColor: c }}
                                    title={`Ubah warna ke ${c}`}
                                  />
                                ))}
                              </div>
                              <input
                                type="color"
                                value={activeRun.color || '#2563EB'}
                                onChange={(e) => handleRecolorRuns([activeIdx], e.target.value)}
                                className="w-6 h-6 rounded cursor-pointer border-0 bg-transparent"
                                title="Pilih custom hex warna"
                              />
                            </div>
                          </div>
                        </div>
                      );
                    })()}

                    {/* Scrollable Runs List */}
                    <div className="divide-y divide-slate-100 flex-1 overflow-y-auto">
                      {result.runs && result.runs.length > 0 ? (
                        result.runs
                          .map((r, idx) => ({ r, idx }))
                          .filter(({ r, idx }) => {
                            if (!searchQuery) return true;
                            const q = searchQuery.toLowerCase();
                            const idStr = (r.id || `run-${idx}`).toLowerCase();
                            const labelStr = (r.label || r.pid || '').toLowerCase();
                            return idStr.includes(q) || labelStr.includes(q) || (r.color || '').toLowerCase().includes(q);
                          })
                          .map(({ r, idx }) => {
                            const isSelected = selectedRunIndices.has(idx);
                            const lengthPx = r.points && r.points.length > 1
                              ? Math.round(
                                  r.points.reduce((acc, p, i, arr) => {
                                    if (i === 0) return 0;
                                    return acc + Math.hypot(p[0] - arr[i - 1][0], p[1] - arr[i - 1][1]);
                                  }, 0)
                                )
                              : 0;

                            return (
                              <div
                                key={r.id || `run-${idx}`}
                                ref={(el) => { runRowRefs.current[idx] = el; }}
                                onClick={() => handleSelectRun(r, idx)}
                                className={`p-3 cursor-pointer transition text-xs group ${
                                  isSelected
                                    ? 'bg-indigo-50 border-l-4 border-indigo-600'
                                    : 'hover:bg-slate-50'
                                }`}
                              >
                                <div className="flex items-center justify-between">
                                  <div className="flex items-center space-x-2 min-w-0">
                                    <input
                                      type="checkbox"
                                      checked={isSelected}
                                      onChange={(e) => {
                                        e.stopPropagation();
                                        const next = new Set(selectedRunIndices);
                                        if (e.target.checked) {
                                          next.add(idx);
                                        } else {
                                          next.delete(idx);
                                        }
                                        setSelectedRunIndices(next);
                                      }}
                                      className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                                    />
                                    <span
                                      className="w-3 h-3 rounded-full shrink-0 shadow-sm border border-white"
                                      style={{ backgroundColor: r.color || '#2563EB' }}
                                    />
                                    <div className="min-w-0">
                                      <div className="flex items-center space-x-1.5">
                                        <span className="font-bold text-slate-900 font-mono truncate">
                                          {r.id || `run-${idx}`}
                                        </span>
                                        {r.manual && (
                                          <span className="text-[9px] bg-amber-100 text-amber-700 font-bold px-1 rounded">
                                            manual
                                          </span>
                                        )}
                                        {r.equipment_outline && (
                                          <span className="text-[9px] bg-orange-100 text-orange-700 font-bold px-1 rounded">
                                            outline
                                          </span>
                                        )}
                                      </div>
                                      <p className="text-[11px] text-slate-500 truncate">
                                        {r.label || r.pid ? (
                                          <span className="text-slate-700 font-semibold">{r.label || r.pid}</span>
                                        ) : (
                                          <span className="text-slate-400 italic">tanpa label</span>
                                        )}
                                      </p>
                                    </div>
                                  </div>

                                  <div className="flex items-center space-x-2 shrink-0">
                                    <span className="text-[11px] font-mono text-slate-400">
                                      {lengthPx > 0 ? `${lengthPx}px` : `${r.points?.length || 2} pts`}
                                    </span>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        if (confirm(`Hapus pipa ${r.id || `run-${idx}`}?`)) {
                                          handleDeleteRuns([idx]);
                                        }
                                      }}
                                      className="text-slate-300 hover:text-rose-600 p-1 rounded hover:bg-rose-50 transition"
                                      title="Hapus pipa"
                                    >
                                      <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                  </div>
                                </div>
                              </div>
                            );
                          })
                      ) : (
                        <div className="p-8 text-center text-slate-400 text-xs">
                          Belum ada data pipa yang terdeteksi.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Sub-tab 3: Symbols / Valves / Equipment */}
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

      {/* Export Modal */}
      {showExportModal && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          onClick={() => setShowExportModal(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6 space-y-4 max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-2">
                <Download className="w-5 h-5 text-indigo-600" />
                <h2 className="text-lg font-bold text-slate-800">Export Deliverables</h2>
              </div>
              <button
                onClick={() => setShowExportModal(false)}
                className="p-1.5 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition"
                title="Tutup"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Active view context banner */}
            <div className="flex items-start space-x-2 bg-indigo-50 border border-indigo-100 rounded-xl px-3 py-2.5">
              <Layers className="w-4 h-4 text-indigo-600 mt-0.5 shrink-0" />
              <div className="text-xs text-indigo-900">
                <p className="font-semibold">
                  Mode aktif:{
                    mode === 'system'
                      ? ' Corrosion System'
                      : mode === 'circuit'
                      ? ' Corrosion Circuit'
                      : mode === 'topology'
                      ? ' Multi-Sheet Topology'
                      : mode === 'report'
                      ? ' Engineering Audit'
                      : ' Digitization'
                  }
                </p>
                <p className="text-indigo-700/80 mt-0.5">
                  {exportMode === 'system'
                    ? 'Export gambar (PDF/PNG) akan berisi pewarnaan Corrosion System sesuai tampilan saat ini.'
                    : exportMode === 'circuit'
                    ? 'Export gambar (PDF/PNG) akan berisi pewarnaan Corrosion Circuit sesuai tampilan saat ini.'
                    : 'Export gambar (PDF/PNG) akan berisi pewarnaan per-pipa (Digitization).'}
                </p>
              </div>
            </div>

            {/* Export options */}
            <div className="space-y-2">
              {[
                {
                  format: 'png' as const,
                  icon: <Download className="w-5 h-5 text-emerald-600" />,
                  title: 'Marked PNG Image (.png)',
                  desc: 'Gambar P&ID ter-marking resolusi tinggi beserta legend, sesuai mode aktif.',
                  modeAware: true,
                },
                {
                  format: 'pdf' as const,
                  icon: <Download className="w-5 h-5 text-red-600" />,
                  title: 'Acrobat Vector PDF (.pdf)',
                  desc: 'PDF vektor siap cetak dengan penandaan warna sesuai mode aktif.',
                  modeAware: true,
                },
                {
                  format: 'xlsx' as const,
                  icon: <FileSpreadsheet className="w-5 h-5 text-green-600" />,
                  title: 'Excel Line Register (.xlsx)',
                  desc: 'Register seluruh line pipa (tag, fluid, ukuran, material) dalam bentuk spreadsheet.',
                  modeAware: false,
                },
                {
                  format: 'docx' as const,
                  icon: <FileSpreadsheet className="w-5 h-5 text-blue-600" />,
                  title: 'Word Asset Register (.docx)',
                  desc: 'Dokumen Word berisi daftar aset & piping untuk keperluan dokumentasi engineering.',
                  modeAware: false,
                },
              ].map((opt) => (
                <a
                  key={opt.format}
                  href={getExportUrl(
                    projectId,
                    activeSheet!.id,
                    opt.format,
                    opt.modeAware ? exportMode : 'engineer'
                  )}
                  download
                  onClick={() => setShowExportModal(false)}
                  className="flex items-start space-x-3 p-3 rounded-xl border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50/50 transition group"
                >
                  <div className="mt-0.5 shrink-0">{opt.icon}</div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-800 group-hover:text-indigo-700">
                      {opt.title}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">{opt.desc}</p>
                    {opt.modeAware && (
                      <span className="inline-block mt-1 text-[10px] font-semibold uppercase tracking-wider text-indigo-600 bg-indigo-100 rounded px-1.5 py-0.5">
                        Mengikuti mode aktif
                      </span>
                    )}
                  </div>
                  <Download className="w-4 h-4 text-slate-300 group-hover:text-indigo-500 mt-1 shrink-0" />
                </a>
              ))}
            </div>
          </div>
        </div>
      )}

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
