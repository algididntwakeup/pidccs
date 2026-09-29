import { Dispatch, SetStateAction, useCallback } from 'react';
import { patchResult, traceRegion, updateRunPoints } from '@/lib/api';
import { DigitizationResult, PipeRun, PipingID } from '@/types/schema';

type PushHistory = (
  description: string,
  previousRuns: PipeRun[],
  nextRuns: PipeRun[],
  previousPids: PipingID[],
  nextPids: PipingID[],
) => void;

type PipeTracerOptions = {
  projectId: string;
  sheetId: string | undefined;
  result: DigitizationResult | null;
  setResult: Dispatch<SetStateAction<DigitizationResult | null>>;
  pushHistory: PushHistory;
  selectRunIds: (ids: string[]) => void;
  setTraceTool: (tool: 'pan') => void;
  showToast: (message: string, duration?: number) => void;
};

export function usePipeTracer({
  projectId, sheetId, result, setResult, pushHistory,
  selectRunIds, setTraceTool, showToast,
}: PipeTracerOptions) {
  const handleManualRun = useCallback(async (points: [number, number][]) => {
    if (!result || !projectId || !sheetId || points.length < 2) return;
    const nextRun: PipeRun = {
      id: `manual-run-${Date.now()}`,
      points,
      axis: points.length === 2 ? 'd' : 'poly',
      x1: points[0][0], y1: points[0][1],
      x2: points[points.length - 1][0], y2: points[points.length - 1][1],
      color: '#2563EB',
      manual: true,
      marked: true,
    };
    const nextRuns = [...result.runs, nextRun];
    pushHistory('Tambah pipa manual', [...result.runs], nextRuns, result.piping_ids, result.piping_ids);
    const updated = await patchResult(projectId, sheetId, { ...result, runs: nextRuns });
    setResult(updated);
    const added = updated.runs[updated.runs.length - 1];
    selectRunIds(added?.id ? [added.id] : []);
    setTraceTool('pan');
  }, [result, projectId, sheetId, pushHistory, setResult, selectRunIds, setTraceTool]);

  const handleUpdateRunPoints = useCallback(async (runIdx: number, points: [number, number][]) => {
    if (!result || !projectId || !sheetId || points.length < 2) return;
    try {
      const previousPids = [...result.piping_ids];
      const response = await updateRunPoints(projectId, sheetId, runIdx, points);
      pushHistory(
        `Luruskan / Edit titik pipa #${runIdx}`,
        [...result.runs], response.result.runs, previousPids,
        response.result.piping_ids || previousPids,
      );
      setResult(response.result);
      showToast(`Titik koordinat pipa #${runIdx} berhasil disesuaikan!`, 2500);
    } catch (error) {
      alert('Gagal mengupdate titik pipa: ' + (error instanceof Error ? error.message : 'Server error'));
    }
  }, [result, projectId, sheetId, pushHistory, setResult, showToast]);

  const handleRescan = useCallback(async (bounds: { x1: number; y1: number; x2: number; y2: number }) => {
    if (!projectId || !sheetId || !result) return;
    try {
      const data = await traceRegion(projectId, sheetId, bounds);
      const nextRuns = data.result?.runs || [...result.runs, ...(data.new_runs || [])];
      const stitched = data.stitched_runs_count ?? data.stitched ?? 0;
      pushHistory(
        'Re-scan area pipa', result.runs, nextRuns, result.piping_ids,
        data.result?.piping_ids || result.piping_ids,
      );
      setResult(data.result || { ...result, runs: nextRuns });
      const added = data.new_runs?.length || 0;
      showToast(
        stitched > 0
          ? `Re-scan selesai! ${stitched} pipa tersambung otomatis${added > 0 ? `, ${added} pipa baru` : ''}`
          : `Re-scan selesai! Menambahkan ${added} pipa baru`,
        3000,
      );
      setTraceTool('pan');
    } catch (error) {
      alert('Gagal melakukan re-scan area: ' + (error instanceof Error ? error.message : 'Server error'));
    }
  }, [projectId, sheetId, result, pushHistory, setResult, showToast, setTraceTool]);

  return { handleManualRun, handleUpdateRunPoints, handleRescan };
}
