// PR-FE-2: Inspector store. Single global rune store; pages and the
// jsonReq wrapper read .open to decide whether to attach ?inspect=true.

export interface ExplainSummary {
  stage: "IXSCAN" | "COLLSCAN" | "IXISCAN+FETCH" | "VECTOR_SEARCH" | string;
  nReturned?: number;
  totalDocsExamined?: number;
  executionTimeMillis?: number;
  index_name?: string | null;
  // $vectorSearch annotations
  num_candidates?: number;
  candidates_evaluated?: number;
  model?: string;
  dimensions?: number;
  similarity?: string;
}

export interface AgentTraceEntry {
  node: string;
  duration_ms: number;
  status: "ok" | "error" | "skipped";
  output_keys?: string[];
  error?: string | null;
  metadata?: Record<string, unknown>;
  query?: unknown; // for tool nodes (vector_search, analytics)
}

export interface InspectorPayload {
  operation: string;
  database: string;
  collection: string;
  query: unknown;
  documents: unknown[];
  result_count: number;
  result_bytes: number;
  latency_ms: number;
  index_name: string | null;
  explain: ExplainSummary | null;
  agent_trace: AgentTraceEntry[] | null;
  // Hint used by the trigger chip when no payload has been recorded yet.
  hint?: string;
}

export type InspectorTab = "query" | "result" | "trace" | "explain";

class InspectorStore {
  open = $state(false);
  pinned = $state(false);
  activeTab = $state<InspectorTab>("query");
  payload = $state<InspectorPayload | null>(null);
  history = $state<InspectorPayload[]>([]);

  setPayload(p: InspectorPayload) {
    this.payload = p;
    this.history = [p, ...this.history].slice(0, 10);
  }
  openPanel() { this.open = true; }
  close() { if (!this.pinned) this.open = false; }
  forceClose() { this.open = false; }
  togglePin() { this.pinned = !this.pinned; }
  switchTab(t: InspectorTab) { this.activeTab = t; }
  clear() { this.payload = null; this.history = []; }
}

export const inspector = new InspectorStore();
