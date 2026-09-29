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
  operating_pressure?: number;
  operating_temperature?: number;
  operating_press_barg?: number;
  operating_temp_c?: number;
  design_pressure?: number;
  design_temperature?: number;
  fluid_phase?: string;
  material?: string;
  corrosion_loop?: string;
  corrosion_allowance?: number;
  corrosion_allowance_mm?: number;
  insulation?: string;
}

export interface PipeRun {
  id?: string;
  points: [number, number][];
  axis: 'h' | 'v' | 'd' | 'poly';
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  underline?: boolean;
  color?: string;
  line_style?: 'solid' | 'dashed';
  marked?: boolean;
  label?: string;
  pid?: string;
  fluid?: string;
  manual?: boolean;
  group_id?: string;
  system_group_id?: string;
  circuit_group_id?: string;
  equipment_outline?: boolean;
  length?: number;
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

export interface OffPageConnector {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  direction: 'incoming' | 'outgoing' | 'bidirectional';
  target_drawing: string;
  target_sheet_number: string;
  target_line?: string;
  line_number?: string;
  text?: string;
  run_idx: number;
  piping_id?: string;
  confidence: number;
  manual?: boolean;
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
  opcs?: OffPageConnector[];
  furniture: [number, number, number, number][];
  manual_groups?: ManualGroup[];
}

export interface ManualGroup {
  id: string;
  name: string;
  color: string;
  kind: 'system' | 'circuit';
}

export interface CircuitProvenance {
  circuit_code: string;
  rule: string;
  evidence: string;
  source: 'linelist' | 'material_spec' | 'piping_class' | 'heuristic' | 'user_override';
  confidence: number;
  timestamp: string;
}

export interface CorrosionCircuit {
  code: string;
  material: string;
  classes: string[];
  fluid_phase?: string;
  color: [number, number, number];
  run_idxs: number[];
  pid_idxs: number[];
  operating_summary?: {
    avg_temperature_c?: number;
    avg_pressure_barg?: number;
    corrosion_allowance_mm?: number;
    corrosion_loop?: string;
  };
  provenance?: CircuitProvenance;
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
  latest_job_id?: string;
  created_at: string;
  updated_at: string;
}

export interface JobResponse {
  job_id: string;
  sheet_id: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress_pct: number;
  step: string;
  message: string;
  error?: string | null;
  created_at: string;
  completed_at?: string | null;
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

export interface TopologyNode {
  id: string;
  type: 'sheet' | 'circuit';
  label: string;
  metadata: Record<string, any>;
}

export interface TopologyEdge {
  id: string;
  source_sheet_id: string;
  target_sheet_id: string;
  source_opc_id: string;
  target_opc_id?: string;
  piping_id?: string;
  fluid?: string;
  confidence: number;
}

export interface ProjectCircuit {
  circuit_code: string;
  fluid: string;
  material: string;
  fluid_phase: string;
  color: [number, number, number];
  sheet_ids: string[];
  member_pids: string[];
  total_pipes: number;
  operating_summary?: Record<string, any>;
  provenance?: CircuitProvenance;
}

export interface ProjectTopologyResponse {
  project_id: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  circuits: ProjectCircuit[];
  summary: Record<string, any>;
}

export interface LineListEntry {
  line_number: string;
  material?: string;
  operating_pressure_barg?: number;
  operating_temperature_c?: number;
  design_pressure_barg?: number;
  design_temperature_c?: number;
  fluid_phase?: string;
  insulation?: string;
  corrosion_allowance_mm?: number;
  corrosion_rate_mmpy?: number;
  corrosion_loop?: string;
  service_condition?: string;
  source_file?: string;
}

export interface LineListImportResult {
  filename: string;
  total_rows: number;
  matched_pids: number;
  unmatched_pids: number;
  enriched_sheets: string[];
  entries: LineListEntry[];
}
