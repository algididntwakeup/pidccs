export interface SymbolDetection {
  coarse: 'equipment' | 'instrument' | 'valve' | 'other';
  cls: string;
  conf: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  subtype?: string;
  tag?: string;
  desc?: string;
  manual?: boolean;
}

export interface PipingID {
  pid: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  unit: string;
  size: string;
  fluid: string;
  pclass: string;
  seq: string;
  conf: number;
  run_idx: number;
  extra_runs: number[];
  state: 'attached' | 'leader' | 'none' | 'manual' | 'propagated';
  manual: boolean;
}

export interface PipeRun {
  points: [number, number][];
  axis: 'h' | 'v' | 'd' | 'poly';
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  underline?: boolean;
}

export interface ConnectionPoint {
  codes: [string, string];
  orient: 'h' | 'v';
  x: number;
  y: number;
  divider: boolean;
  in_vocab: boolean;
  conf: number;
  run_idx: number;
  dist?: number;
  side_a?: string;
  side_b?: string;
}

export interface DigitizationResult {
  image_path: string;
  dpi: number;
  rot: number;
  w: number;
  h: number;
  symbols: SymbolDetection[];
  runs: PipeRun[];
  piping_ids: PipingID[];
  conn_points: ConnectionPoint[];
  furniture: [number, number, number, number][];
}

export interface CorrosionCircuit {
  code: string;
  material: string;
  classes: string[];
  color: [number, number, number];
  run_idxs: number[];
  pid_idxs: number[];
}

export interface CorrosionSystem {
  index: number;
  fluid: string;
  color: [number, number, number];
  run_idxs: number[];
  pid_idxs: number[];
  n_pipes: number;
  circuits: CorrosionCircuit[];
}

export interface SheetResponse {
  id: string;
  project_id: string;
  filename: string;
  sheet_number: string;
  file_path: string;
  status: string;
  dpi: number;
  rot: number;
  width?: number;
  height?: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectResponse {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  sheets: SheetResponse[];
}

export interface ValidationCheck {
  id: string;
  name: string;
  value: number;
  detail: string;
  passed: boolean;
}

export interface ValidationReport {
  title?: string;
  header?: string;
  n_symbols: number;
  n_piping_ids: number;
  n_pipes: number;
  score: number;
  passed: boolean;
  n_critical: number;
  n_warnings: number;
  checks: ValidationCheck[];
  flags: Record<string, any>[];
  warnings: Record<string, any>[];
}
