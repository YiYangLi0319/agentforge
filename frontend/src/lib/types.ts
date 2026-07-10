// 与后端 agentforge/core/events.py 对齐的事件契约

export interface Source {
  id: number;
  origin: "kb" | "web";
  title: string;
  snippet: string;
  url?: string;
  chunk_id?: string;
  document_id?: string;
  filename?: string;
  heading?: string;
  verified?: boolean;
}

export interface ToolCallInfo {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface AgentEvent {
  seq?: number;
  type: string;
  ts?: number;
  agent?: string | null;
  // llm_delta
  text?: string;
  channel?: string;
  // assistant_message
  content?: string;
  tool_calls?: ToolCallInfo[];
  final?: boolean;
  // tool events
  tool_call_id?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  ok?: boolean;
  result_preview?: string;
  duration_ms?: number;
  approved?: boolean;
  // step
  step?: number;
  usage?: { prompt_tokens: number; completion_tokens: number };
  // research
  plan?: ResearchPlan;
  task_id?: string;
  title?: string;
  summary?: string;
  evidence_count?: number;
  markdown?: string;
  revision?: number;
  passed?: boolean;
  scores?: Record<string, number>;
  feedback?: string;
  audit?: Record<string, unknown>;
  phase?: string;
  completed_tasks?: number;
  total_tasks?: number;
  // finish
  output?: { text?: string; report?: string; sources?: Source[]; [k: string]: unknown };
  cost?: number;
  error?: string;
  sources?: Source[];
  added?: number;
  // guardrail / cache
  stage?: string;
  verdict?: string;
  categories?: string[];
  detail?: string;
  similarity?: number;
}

export interface DashboardStats {
  range_days: number;
  totals: {
    runs: number;
    success_rate: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost: number;
    avg_latency_s: number;
  };
  by_kind: Record<string, number>;
  by_status: Record<string, number>;
  trend: { day: string; runs: number; tokens: number; cost: number }[];
  tool_usage: { tool: string; count: number }[];
  cache: {
    enabled: boolean;
    threshold: number;
    entries: number;
    session_hits: number;
    session_misses: number;
    hit_rate: number;
    lifetime_hits: number;
  };
  capabilities: {
    llm: { provider: string; model: string };
    embedding: { provider: string; model: string };
    guardrails_enabled: boolean;
    semantic_cache_enabled: boolean;
    mcp_servers: Record<string, string>;
    mcp_tools: number;
    rag: { query_rewrite: boolean; hyde: boolean; compression: boolean; parent_child: boolean };
  };
}

export interface LiveSeriesPoint {
  t: string;
  runs: number;
  tokens: number;
  cost: number;
  cache_hits: number;
  cache_misses: number;
  hit_rate: number | null;
  sse_reconnects: number;
  avg_latency_s: number;
}

export interface LiveSeries {
  minutes: number;
  buckets: number;
  points: LiveSeriesPoint[];
  summary: {
    runs: number;
    tokens: number;
    cost: number;
    cache_hits: number;
    cache_misses: number;
    hit_rate: number | null;
    sse_reconnects: number;
  };
}

export interface EvalRecordInfo {
  id: string;
  dataset: string;
  metrics: Record<string, number>;
  cases: number | null;
  enabled_judge: boolean;
  created_at: string;
}

export interface EvalSuites {
  suites: Record<string, EvalRecordInfo[]>;
}

export interface BuiltinTool {
  name: string;
  description: string;
  requires_approval: boolean;
  tags: string[];
  parameters: Record<string, unknown>;
}

export interface CustomToolParam {
  name: string;
  type: "string" | "number" | "integer" | "boolean";
  required: boolean;
  description: string;
  location: "query" | "path" | "body";
}

export interface CustomToolInfo {
  id: string;
  name: string;
  description: string;
  method: string;
  url_template: string;
  headers: Record<string, string>;
  params_schema: CustomToolParam[];
  body_template: string;
  enabled: boolean;
  timeout: number;
  created_at: string;
}

export interface QuotaInfo {
  used: number;
  limit: number;
  remaining: number | null;
  unlimited: boolean;
  is_admin: boolean;
}

export interface MeInfo {
  user_id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
  quota: QuotaInfo;
}

export interface CustomAgentInfo {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  tools: string[];
  kb_ids: string[];
  max_steps: number;
  temperature: number;
  created_at: string;
}

export interface DatasetInfo {
  id: string;
  name: string;
  filename: string;
  columns: { name: string; type: string }[];
  row_count: number;
  created_at: string;
  preview?: Record<string, unknown>[];
}

export interface AnalyzeResult {
  question: string;
  sql: string;
  summary: string;
  result: { columns: string[]; rows: unknown[][] };
  chart: { type: string; x: string; y: string };
  latency_ms: number;
  error?: string;
}

export interface AdminUser {
  id: string;
  username: string;
  is_admin: boolean;
  quota: number;
  used_today: number;
  total_tokens: number;
  total_cost: number;
  run_count: number;
  created_at: string;
}

export interface ResearchPlan {
  topic: string;
  sub_questions: { id: string; question: string; queries: string[] }[];
}

export interface ChatSessionInfo {
  id: string;
  title: string;
  agent_type: "assistant" | "team" | "custom";
  custom_agent_id?: string | null;
  kb_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface ChatMessageInfo {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  run_id?: string;
  created_at: string;
}

export interface KnowledgeBaseInfo {
  id: string;
  name: string;
  description: string;
  doc_count: number;
  chunk_count: number;
  updated_at: string;
}

export interface DocumentInfo {
  id: string;
  filename: string;
  size: number;
  status: "pending" | "processing" | "ready" | "failed";
  error: string;
  chunk_count: number;
  created_at: string;
}

export interface RetrievedChunkInfo {
  chunk_id: string;
  document_id: string;
  filename: string;
  heading: string;
  content: string;
  vector_score: number;
  bm25_score: number;
  rrf_score: number;
  rerank_score: number | null;
  final_score: number;
}

export interface RunSummary {
  id: string;
  kind: "chat" | "research";
  status: string;
  input_preview: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number;
  created_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface SpanInfo {
  id: string;
  parent_id: string | null;
  name: string;
  kind: "agent" | "llm" | "tool" | "retrieval" | "chain";
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
}

export interface ResearchReportInfo {
  id: string;
  run_id: string;
  query: string;
  status: string;
  plan?: ResearchPlan;
  report_md?: string;
  sources?: Source[];
  review?: Record<string, unknown>;
  created_at: string;
}
