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

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface StepAssertion {
  check: string
  passed: boolean
  detail: string
}

export interface SimulationStep {
  step_id: string
  name: string
  description: string
  verdict: string
  reason_codes: string[]
  elapsed_ms: number
  assertions: StepAssertion[]
  explanation: string
  metadata: Record<string, unknown>
}

export interface SimulationScenario {
  scenario_id: string
  title: string
  category: string
  threat_class: string | null
  target_gate: string
  summary: string
  expected_decision: string
  default_tier: string
  default_amount_inr: number
}

export interface SimulationResult {
  scenario_id: string
  scenario_title: string
  threat_class: string | null
  category: string
  summary: string
  request_summary: {
    method: string
    path: string
    agent_id: string
    tier: string
    cart_total_inr: number
    items: Array<{ sku: string; name: string; qty: number; price_inr: number }>
    has_signature: boolean
    free_text: Record<string, string>
    callback_url: string | null
  }
  raw_request: Record<string, unknown>
  decision: string
  reason_codes: string[]
  explanation: string
  total_latency_ms: number
  obligation: {
    obligation_id: string
    receipt_hash: string
    amount_due_inr: number
    currency: string
    merchant_id: string
    rail_type: string
    ledger_tip: string
    ledger_entries: number
  } | null
  steps: SimulationStep[]
}

export interface DisputeSummary {
  package_id: string
  dispute_id: string
  obligation_id: string
  created_at: string
  executive_summary: string
  assigned_fault: string
  outcome: string
  confidence: number
  reason_codes: string[]
  has_certificate: boolean
  has_consent: boolean
}

export interface DisputePackageDetail {
  package_id: string
  dispute_id: string
  obligation_id: string
  created_at: string
  executive_summary: string
  liability_verdict: {
    verdict_id: string
    assigned_fault: string
    fault_allocation: Record<string, number>
    outcome: string
    confidence: number
    reason_codes: string[]
    explanation: string
    compelling_evidence_summary: string
  }
  settlement_certificate: {
    certificate_id: string
    obligation_id: string
    version: number
    merchant_id: string
    agent_id: string
    principal_ref: string
    rail: { type: string; ref: string; simulated: boolean }
    clearing_decision_hash: string
    aggregate_basis: string
    performance_verdict: string
    finality: string
    evidence_item_hashes: string[]
    issued_at: string
    certificate_hash: string
    merchant_signature: string
  } | null
  consent_record: {
    consent_id: string
    principal_ref: string
    agent_id: string
    intent_id: string
    intent_hash: string
    cart_id: string
    cart_hash: string
    mandate_chain_hash: string
    constraints: {
      max_amount: number
      allowed_merchants: string[]
      allowed_categories: string[] | null
    }
    delegation_signature: string
    issued_at: string
    expires_at: string
    anchored_rail_ref: string | null
    consent_hash: string
  } | null
  obligation_receipt: {
    obligation_id: string
    merchant_id: string
    agent_id: string
    principal_ref: string
    promised: {
      line_items: Array<{ sku: string; name: string; qty: number; unit_price: number }>
      total: number
      currency: string
    }
    admissibility_floor: string
    mandate_chain_hash: string
    rail: { type: string; ref: string; simulated: boolean }
    self_hash: string
  }
  razorpay_anchor_proof: Record<string, unknown>
  audit_trail_hash_chain: Array<{ step: string; entity_id: string; hash: string; timestamp: string }>
  representment_brief_markdown: string
}

export interface AgentReputationResponse {
  agent_id: string
  credit_score: number
  risk_band: string
  network_cleared_volume: number
  cross_merchant_cleared_count: number
  cross_merchant_dispute_rate: number
  distinct_merchants_count: number
  reputation_tier: string
  attestations_count: number
  calculated_at: string
}

export const api = {
  health: () => get<HealthResponse>('/health'),
  decisions: () => get<DecisionSummary[]>('/decisions'),
  decision: (id: string) => get<DecisionDetail>(`/decisions/${id}`),
  metrics: () => get<MetricsResponse>('/metrics'),
  benchmark: () => get<BenchmarkResponse>('/benchmark'),
  ledger: () => get<LedgerVerification>('/ledger/verify'),
  simulationScenarios: () => get<SimulationScenario[]>('/simulation/scenarios'),
  runSimulation: (params?: { scenario_id?: string; custom_params?: Record<string, unknown> }) =>
    post<SimulationResult>('/simulation/run', params),
  disputes: () => get<DisputeSummary[]>('/disputes'),
  dispute: (id: string) => get<DisputePackageDetail>(`/disputes/${id}`),
  evaluateDispute: (claim: Record<string, unknown>) =>
    post<DisputePackageDetail>('/disputes/evaluate', claim),
  reputation: (agentId: string) => get<AgentReputationResponse>(`/reputation/${agentId}`),
  normalizeCrossRail: (payload: Record<string, unknown>) =>
    post<{ token: Record<string, unknown>; is_valid: boolean; rail_normalized: boolean }>(
      '/cross-rail/normalize',
      payload
    ),
}
