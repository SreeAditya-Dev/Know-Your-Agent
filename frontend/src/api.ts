const BASE = '/v1'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface HealthResponse {
  status: string
  mode: string
  decisions: number
  ledger_entries: number
}

export interface DecisionSummary {
  decision_id: string
  decision: string
  agent_id: string
  reason_codes: string[]
  obligation_id: string | null
  latency_ms: number
  decided_at: string
  explanation: string
}

export interface GateResult {
  gate: string
  verdict: string
  codes: string[]
  detail: Record<string, unknown>
  elapsed_ms: number
}

export interface DecisionEnvelope {
  decision_id: string
  decision: string
  agent_id: string
  tier: string
  reason_codes: string[]
  gate_trace: GateResult[]
  explanation: string
  obligation_id: string | null
  idempotent_replay: boolean
  latency_ms: number
  policy_version: string
  decided_at: string
}

export interface DecisionDetail {
  request: Record<string, unknown>
  decision: DecisionEnvelope
  result: Record<string, unknown> | null
}

export interface MetricsResponse {
  benchmark: {
    sessions: number
    attacks: number
    recall: number
    precision: number
    p99: number
  }
  operations: {
    decisions: number
    quarantined: number
    obligations: number
    ledger_ok: boolean
    ledger_entries: number
    clearing_disputed: number
  }
}

export interface LedgerVerification {
  ok: boolean
  entries: number
  tip_hash: string
  failures: Array<{ index: number; detail: string }>
}

export interface BaselineEntry {
  tp: number
  fp: number
  fn: number
  tn: number
  recall: number
  precision: number
  fpr: number
  latency_p99: number
}

export interface BenchmarkResponse {
  n_sessions: number
  n_attacks: number
  corpus_hash: string
  baselines: Record<string, BaselineEntry>
}

export const api = {
  health: () => get<HealthResponse>('/health'),
  decisions: () => get<DecisionSummary[]>('/decisions'),
  decision: (id: string) => get<DecisionDetail>(`/decisions/${id}`),
  metrics: () => get<MetricsResponse>('/metrics'),
  benchmark: () => get<BenchmarkResponse>('/benchmark'),
  ledger: () => get<LedgerVerification>('/ledger/verify'),
}
