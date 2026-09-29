import { Dispatch, SetStateAction, useCallback, useReducer, useState } from 'react';
import { patchResult } from '@/lib/api';
import { DigitizationResult, PipeRun, PipingID } from '@/types/schema';

type HistoryEntry = {
  desc: string;
  prevRuns: PipeRun[];
  nextRuns: PipeRun[];
  prevPids: PipingID[];
  nextPids: PipingID[];
};

type HistoryState = { entries: HistoryEntry[]; index: number };
type HistoryAction = { type: 'push'; entry: HistoryEntry } | { type: 'undo' } | { type: 'redo' };

function historyReducer(state: HistoryState, action: HistoryAction): HistoryState {
  if (action.type === 'push') {
    const entries = [...state.entries.slice(0, state.index + 1), action.entry].slice(-20);
    return { entries, index: entries.length - 1 };
  }
  if (action.type === 'undo' && state.index >= 0) return { ...state, index: state.index - 1 };
  if (action.type === 'redo' && state.index < state.entries.length - 1) {
    return { ...state, index: state.index + 1 };
  }
  return state;
}

export function useHistory(
  result: DigitizationResult | null,
  setResult: Dispatch<SetStateAction<DigitizationResult | null>>,
  projectId: string,
  sheetId: string | undefined,
  showToast: (message: string, duration?: number) => void,
) {
  const [history, dispatch] = useReducer(historyReducer, { entries: [], index: -1 });
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  const pushHistory = useCallback(
    (desc: string, prevRuns: PipeRun[], nextRuns: PipeRun[], prevPids: PipingID[], nextPids: PipingID[], alreadyPersisted = false) => {
      dispatch({ type: 'push', entry: { desc, prevRuns, nextRuns, prevPids, nextPids } });
      if (!alreadyPersisted) setHasUnsavedChanges(true);
    },
    [],
  );

  const restore = useCallback((entry: HistoryEntry, direction: 'undo' | 'redo') => {
    if (!result || !projectId || !sheetId) return;
    const restored = {
      ...result,
      runs: direction === 'undo' ? entry.prevRuns : entry.nextRuns,
      piping_ids: direction === 'undo' ? entry.prevPids : entry.nextPids,
    };
    setResult(restored);
    // Run indices are mutable on the server. Keep the persisted array in sync
    // before the next split/delete request that addresses a run by index.
    patchResult(projectId, sheetId, restored).catch((error) =>
      console.error(`Gagal sinkronisasi ${direction} ke server:`, error),
    );
  }, [result, projectId, sheetId, setResult]);

  const handleUndo = useCallback(() => {
    const entry = history.entries[history.index];
    if (!entry) return;
    restore(entry, 'undo');
    dispatch({ type: 'undo' });
    showToast(`Undo: ${entry.desc}`, 2000);
  }, [history, restore, showToast]);

  const handleRedo = useCallback(() => {
    const entry = history.entries[history.index + 1];
    if (!entry) return;
    restore(entry, 'redo');
    dispatch({ type: 'redo' });
    showToast(`Redo: ${entry.desc}`, 2000);
  }, [history, restore, showToast]);

  return {
    historyIndex: history.index,
    historyLength: history.entries.length,
    pushHistory,
    handleUndo,
    handleRedo,
    hasUnsavedChanges,
    setHasUnsavedChanges,
  };
}
