/**
 * TypeScript Contracts for Autonomous AI ERP Reporting System
 * Aligned with FastAPI Orchestrator schemas (app/schemas/api_models.py & report_plan.py)
 */

export type VisualizationType =
  | 'table'
  | 'bar_chart'
  | 'line_chart'
  | 'donut_chart'
  | 'kpi_grid';

export type ColumnDataType =
  | 'string'
  | 'number'
  | 'currency'
  | 'date'
  | 'boolean';

export type TrendSentiment = 'positive' | 'negative' | 'neutral';

export interface ColumnDef {
  key: string;
  label: string;
  type: ColumnDataType;
  align?: 'left' | 'center' | 'right';
  sortable?: boolean;
  metric_key?: string;
}

export interface KPICard {
  label: string;
  value: string | number;
  trend?: TrendSentiment | null;
  delta?: string | null;
  subtext?: string | null;
  metric_key?: string;
}

export interface FilterItem {
  key: string;
  label?: string;
  operator: string;
  value: string | number | boolean;
}

export interface UISchema {
  suggested_visualization: VisualizationType;
  columns: ColumnDef[];
  kpis: KPICard[];
  filters: FilterItem[];
}

export interface RetryHistoryItem {
  attempt: number;
  error_message: string;
  self_corrected_sql?: string;
  timestamp?: string;
}

export interface ExecutionMetadata {
  thought_process: string;
  tables_used: string[];
  metrics_applied: string[];
  verified_sql: string;
  retries_used: number;
  retry_history?: RetryHistoryItem[];
  latency_ms: number;
  llm_latency_ms?: number;
  db_latency_ms?: number;
  ast_latency_ms?: number;
}

export interface ReportQueryRequest {
  tenant_id?: string;
  user_query: string;
  module_scope?: string | string[];
  session_id?: string;
  parameters?: Record<string, any>;
}

export interface ReportQueryResponse {
  status: 'success' | 'refusal' | 'error';
  execution_metadata: ExecutionMetadata;
  ui_schema: UISchema;
  data: Record<string, any>[];
  error?: string | null;
}

export interface MetricDefinition {
  key: string;
  label: string;
  canonical_sql: string;
  required_tables: string[];
  description: string;
  source_reference?: string;
}
