export interface StageRow {
  key?: string;
  label: string;
  done: number;
  total: number;
  pct: number;
  badge: string;
  current: string;
}

export interface Status {
  phase: string;
  message: string;
  materials: number;
  nodes: number;
  edges: number;
  active: number;
  engine: string;
  embedding_server: string;
  embedding_backend: string;
  errors: string[];
  rows: StageRow[];
}
