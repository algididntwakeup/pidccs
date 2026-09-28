import { ProjectResponse, SheetResponse, DigitizationResult, CorrosionSystem, ValidationReport, JobResponse, TraceClickResponse } from '@/types/schema';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function fetchProjects(): Promise<ProjectResponse[]> {
  const res = await fetch(`${API_BASE}/api/v1/projects`);
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function createProject(name: string, description: string = ''): Promise<ProjectResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  });
  if (!res.ok) throw new Error('Failed to create project');
  return res.json();
}

export async function fetchProject(projectId: string): Promise<ProjectResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}`);
  if (!res.ok) throw new Error('Failed to fetch project');
  return res.json();
}

export async function deleteProject(projectId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete project');
}

export async function deleteSheet(projectId: string, sheetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete sheet');
}

export async function fetchSheets(projectId: string): Promise<SheetResponse[]> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets`);
  if (!res.ok) throw new Error('Failed to fetch sheets');
  return res.json();
}

export async function uploadSheet(projectId: string, file: File, dpi: number = 350): Promise<SheetResponse[]> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('dpi', dpi.toString());

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error('Failed to upload drawing sheet');
  // A multi-page PDF is split server-side into one sheet per page, so the response
  // is always a list.
  return res.json();
}

export async function triggerDetection(
  projectId: string,
  sheetId: string,
  dpi?: number,
  rot?: number,
  mode: 'full' | 'lines_only' = 'full'
): Promise<JobResponse> {
  const params = new URLSearchParams();
  if (dpi) params.append('dpi', dpi.toString());
  if (rot !== undefined) params.append('rot', rot.toString());
  params.append('mode', mode);

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/detect?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, dpi, rot }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to trigger detection');
  }
  return res.json();
}

export async function triggerEnrichment(
  projectId: string,
  sheetId: string,
  dpi?: number,
  rot?: number
): Promise<JobResponse> {
  const params = new URLSearchParams();
  if (dpi) params.append('dpi', dpi.toString());
  if (rot !== undefined) params.append('rot', rot.toString());

  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/enrich?${params}`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to trigger enrichment');
  }
  return res.json();
}


export async function fetchJob(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`);
  if (!res.ok) throw new Error('Failed to fetch job status');
  return res.json();
}

/** Most recent detection job for a sheet, or null if none. Used to resume
 *  observing an in-flight detection after navigation/remount. */
export async function fetchLatestJobForSheet(sheetId: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_BASE}/api/v1/jobs?sheet_id=${encodeURIComponent(sheetId)}`);
  if (!res.ok) throw new Error('Failed to fetch jobs for sheet');
  const jobs: JobResponse[] = await res.json();
  return jobs && jobs.length > 0 ? jobs[0] : null;
}

export async function fetchResult(projectId: string, sheetId: string): Promise<DigitizationResult> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result`);
  if (!res.ok) throw new Error('Failed to fetch sheet result');
  return res.json();
}

export async function patchResult(projectId: string, sheetId: string, updatedResult: DigitizationResult): Promise<DigitizationResult> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updatedResult),
  });
  if (!res.ok) throw new Error('Failed to patch result');
  return res.json();
}

export async function fetchSystems(projectId: string, sheetId: string): Promise<CorrosionSystem[]> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/systems`);
  if (!res.ok) throw new Error('Failed to fetch corrosion systems');
  return res.json();
}

export async function fetchValidation(projectId: string, sheetId: string): Promise<ValidationReport> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/validate`);
  if (!res.ok) throw new Error('Failed to fetch validation report');
  return res.json();
}

export function getExportUrl(
  projectId: string,
  sheetId: string,
  format: 'xlsx' | 'docx' | 'pdf' | 'png',
  mode: 'system' | 'circuit' | 'engineer' = 'engineer'
): string {
  return `${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/export?format=${format}&mode=${mode}`;
}

export function getRawImageUrl(projectId: string, sheetId: string): string {
  return `${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/raw`;
}

export function getThumbnailUrl(projectId: string, sheetId: string, size: number = 480): string {
  return `${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/thumbnail?size=${size}`;
}

export function getMarkedImageUrl(projectId: string, sheetId: string, mode: 'system' | 'circuit' | 'engineer' = 'engineer'): string {
  return `${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/export?format=png&mode=${mode}`;
}

export async function splitRun(projectId: string, sheetId: string, runIdx: number, x: number, y: number) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/${runIdx}/split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x, y }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to split pipe run' }));
    throw new Error(err.detail || 'Failed to split pipe run');
  }
  return res.json();
}

export async function updateRunColor(projectId: string, sheetId: string, runIdx: number, color: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/${runIdx}/color`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ color }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to update run color' }));
    throw new Error(err.detail || 'Failed to update run color');
  }
  return res.json();
}

export async function batchUpdateRunColors(projectId: string, sheetId: string, runIdxs: number[], color: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/batch-color`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_idxs: runIdxs, color }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to batch update run colors' }));
    throw new Error(err.detail || 'Failed to batch update run colors');
  }
  return res.json();
}

export async function deleteRun(projectId: string, sheetId: string, runIdx: number) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/${runIdx}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to delete run' }));
    throw new Error(err.detail || 'Failed to delete run');
  }
  return res.json();
}

export async function batchDeleteRuns(projectId: string, sheetId: string, runIdxs: number[]) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_idxs: runIdxs }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to batch delete runs' }));
    throw new Error(err.detail || 'Failed to batch delete runs');
  }
  return res.json();
}

export async function updateRunLabel(projectId: string, sheetId: string, runIdx: number, label: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/${runIdx}/label`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to update run label' }));
    throw new Error(err.detail || 'Failed to update run label');
  }
  return res.json();
}

export async function updateRunPoints(projectId: string, sheetId: string, runIdx: number, points: [number, number][]) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/result/runs/${runIdx}/points`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ points }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to update run points' }));
    throw new Error(err.detail || 'Failed to update run points');
  }
  return res.json();
}

export async function traceRegion(
  projectId: string,
  sheetId: string,
  bounds: { x1: number; y1: number; x2: number; y2: number }
) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/trace-region`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...bounds, sheet_id: sheetId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to trace region' }));
    throw new Error(err.detail || 'Failed to trace region');
  }
  return res.json();
}

export async function traceClick(
  projectId: string,
  sheetId: string,
  x: number,
  y: number,
  radius: number = 15
): Promise<TraceClickResponse> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/trace-click`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x, y, radius }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Magic Wand gagal' }));
    throw new Error(err.detail || 'Magic Wand gagal');
  }
  return res.json();
}

export function getDziUrl(projectId: string, sheetId: string): string {
  return `${API_BASE}/api/v1/projects/${projectId}/sheets/${sheetId}/dzi`;
}

export async function uploadLineList(projectId: string, file: File) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/linelist`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error('Failed to upload line list spreadsheet');
  return res.json();
}

export async function fetchLineList(projectId: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/linelist`);
  if (!res.ok) throw new Error('Failed to fetch project line list');
  return res.json();
}

export async function fetchProjectTopology(projectId: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/topology`);
  if (!res.ok) throw new Error('Failed to fetch project topology');
  return res.json();
}

export async function fetchProjectCircuits(projectId: string) {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/circuits`);
  if (!res.ok) throw new Error('Failed to fetch project circuits');
  return res.json();
}