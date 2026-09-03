import { useState, useEffect } from 'react'
import { api } from '../api'
import type { DisputeSummary, DisputePackageDetail, AgentReputationResponse } from '../api'
import { Panel } from '../components/Panel'
import { StatGrid } from '../components/StatGrid'
import { Loader, ErrorBox } from '../components/Loader'
import {
  ShieldCheck,
  Scale,
  UserCheck,
  Bot,
  ChevronRight,
  Sparkles,
} from 'lucide-react'

export function DisputesPage() {
  const [disputes, setDisputes] = useState<DisputeSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedDetail, setSelectedDetail] = useState<DisputePackageDetail | null>(null)
  const [reputation, setReputation] = useState<AgentReputationResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [evaluating, setEvaluating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Custom simulation form state
  const [disputeReason, setDisputeReason] = useState('unauthorized_agent_spend')
  const [claimant, setClaimant] = useState('BUYER_PRINCIPAL')
  const [disputedAmount, setDisputedAmount] = useState(7499)
  const [details, setDetails] = useState('Buyer claims they did not authorize agent to buy wireless headphones.')

  const loadData = async () => {
    try {
      setLoading(true)
      const [disputeList, repData] = await Promise.all([
        api.disputes(),
        api.reputation('agent_demo').catch(() => null),
      ])
      setDisputes(disputeList)
      if (disputeList.length > 0) {
        const firstId = disputeList[0].dispute_id
        setSelectedId(firstId)
        const detail = await api.dispute(firstId)
        setSelectedDetail(detail)
      }
      setReputation(repData)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const handleSelectDispute = async (id: string) => {
    setSelectedId(id)
    try {
      const detail = await api.dispute(id)
      setSelectedDetail(detail)
    } catch (err) {
      console.error(err)
    }
  }

  const handleRunEvaluation = async () => {
    if (!selectedDetail) return
    try {
      setEvaluating(true)
      const claim = {
        dispute_id: `dsp_sim_${Math.random().toString(36).substring(2, 10)}`,
        obligation_id: selectedDetail.obligation_id,
        claimant,
        claim_reason: disputeReason,
        disputed_amount: disputedAmount * 100,
        details,
      }
      const newPkg = await api.evaluateDispute(claim)
      const updatedList = await api.disputes()
      setDisputes(updatedList)
      setSelectedId(newPkg.dispute_id)
      setSelectedDetail(newPkg)
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    } finally {
      setEvaluating(false)
    }
  }

  if (loading) return <Loader />
  if (error) return <ErrorBox message={error} />

  const protectedCount = disputes.filter(d => d.outcome === 'MERCHANT_PROTECTED').length
  const refundCount = disputes.filter(d => d.outcome === 'REFUND_ISSUED').length
  const agentFaultCount = disputes.filter(d => d.outcome === 'AGENT_FAULT_ESCROW_CLAIM').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <section className="flex justify-between gap-5 items-end max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-muted-foreground/70 font-mono text-[11px] font-medium tracking-widest uppercase">
            Disputes & Consent Engine
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight flex items-center gap-2.5">
            <Scale className="text-primary" size={26} />
            Liability Arbiter & Consent Evidence Chain
          </h1>
          <p className="text-muted-foreground/70 text-sm mt-1 max-w-3xl">
            Solves the industry liability gap where Visa/Mastercard rules assume human buyers.
            KYA uses cryptographic <strong>Consent Ledgers</strong>, <strong>Obligation Receipts</strong>, and <strong>Verification Mesh Proofs</strong> to deterministically assign fault and auto-compile Visa Compelling Evidence 3.0 packages.
          </p>
        </div>
      </section>

      {/* High level stats */}
      <StatGrid
        items={[
          { label: 'Disputes Adjudicated', value: disputes.length },
          { label: 'Merchant Protected', value: protectedCount, warn: false },
          { label: 'Refunds / Merchant Fault', value: refundCount },
          { label: 'Agent Operator Fault', value: agentFaultCount },
        ]}
      />

      {/* Agent Reputation Card */}
      {reputation && (
        <div className="bg-gradient-to-r from-slate-900 via-slate-800 to-indigo-950 rounded-lg p-5 text-white shadow-sm border border-slate-700/50">
          <div className="flex justify-between items-center flex-wrap gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Bot className="text-indigo-400" size={20} />
                <span className="font-mono text-xs text-indigo-300 uppercase tracking-wider">
                  Cross-Merchant Agent Credit Score
                </span>
                <span className="bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-2 py-0.5 text-[11px] font-mono rounded">
                  {reputation.risk_band}
                </span>
              </div>
              <h2 className="text-xl font-bold mt-1">
                Agent ID: <span className="font-mono text-indigo-200">{reputation.agent_id}</span>
              </h2>
            </div>
            <div className="flex items-center gap-6">
              <div className="text-right">
                <div className="text-xs text-slate-400">Network Cleared Volume</div>
                <div className="text-lg font-semibold font-mono">
                  ₹{(reputation.network_cleared_volume / 100).toLocaleString('en-IN')}
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs text-slate-400">Cross-Merchant Dispute Rate</div>
                <div className="text-lg font-semibold font-mono text-emerald-400">
                  {(reputation.cross_merchant_dispute_rate * 100).toFixed(1)}%
                </div>
              </div>
              <div className="bg-slate-800/80 border border-indigo-500/40 rounded-xl px-4 py-2 text-center min-w-[100px]">
                <div className="text-[10px] uppercase font-mono text-slate-400">Credit Score</div>
                <div className="text-2xl font-bold font-mono text-indigo-400">
                  {reputation.credit_score}
                  <span className="text-xs text-slate-400 font-normal">/1000</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Grid: Dispute List + Dispute Inspector */}
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] max-lg:grid-cols-1 gap-6">
        {/* Left: Dispute Cases */}
        <div className="space-y-4">
          <Panel title="Adjudicated Disputes" meta={`${disputes.length} cases`}>
            <div className="divide-y divide-line-soft">
              {disputes.length === 0 ? (
                <div className="p-6 text-center text-muted-foreground/70 text-sm">
                  No dispute claims recorded.
                </div>
              ) : (
                disputes.map((d) => {
                  const isSelected = d.dispute_id === selectedId
                  return (
                    <div
                      key={d.dispute_id}
                      onClick={() => handleSelectDispute(d.dispute_id)}
                      className={`p-4 transition-colors cursor-pointer hover:bg-slate-50 flex items-start justify-between gap-3 ${
                        isSelected ? 'bg-slate-100/80 border-l-4 border-primary' : ''
                      }`}
                    >
                      <div className="space-y-1 flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-semibold text-slate-800">
                            {d.dispute_id}
                          </span>
                          <span className="text-xs text-muted-foreground/70">·</span>
                          <span className="font-mono text-[11px] text-muted-foreground/70">
                            {d.obligation_id}
                          </span>
                        </div>
                        <p className="text-xs text-slate-700 font-medium line-clamp-2">
                          {d.executive_summary}
                        </p>
                        <div className="flex items-center gap-2 pt-1 flex-wrap">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                              d.outcome === 'MERCHANT_PROTECTED'
                                ? 'bg-emerald-100 text-emerald-800'
                                : d.outcome === 'REFUND_ISSUED'
                                ? 'bg-amber-100 text-amber-800'
                                : 'bg-purple-100 text-purple-800'
                            }`}
                          >
                            {d.outcome}
                          </span>
                          <span className="text-[11px] text-slate-500 font-mono">
                            Fault: <strong>{d.assigned_fault}</strong> ({Math.round(d.confidence * 100)}%)
                          </span>
                        </div>
                      </div>
                      <ChevronRight size={18} className="text-slate-400 mt-1 shrink-0" />
                    </div>
                  )
                })
              )}
            </div>
          </Panel>

          {/* Interactive Simulation Panel */}
          <Panel title="Simulate Inbound Dispute Claim" meta="Test Arbiter Response">
            <div className="p-4 space-y-3 text-xs">
              <div>
                <label className="block text-slate-600 font-medium mb-1">Claimant Party</label>
                <select
                  value={claimant}
                  onChange={(e) => setClaimant(e.target.value)}
                  className="w-full border border-border/50 rounded p-1.5 text-xs bg-white"
                >
                  <option value="BUYER_PRINCIPAL">BUYER_PRINCIPAL (Human Buyer)</option>
                  <option value="AGENT_OPERATOR">AGENT_OPERATOR (AI Provider)</option>
                  <option value="PAYMENT_RAIL">PAYMENT_RAIL (Card Network)</option>
                </select>
              </div>

              <div>
                <label className="block text-slate-600 font-medium mb-1">Dispute Reason</label>
                <select
                  value={disputeReason}
                  onChange={(e) => setDisputeReason(e.target.value)}
                  className="w-full border border-border/50 rounded p-1.5 text-xs bg-white"
                >
                  <option value="unauthorized_agent_spend">Unauthorized Agent Spend (Friendly Fraud)</option>
                  <option value="not_as_described">Not As Described / Wrong SKU (Merchant Breach)</option>
                  <option value="merchandise_not_received">Merchandise Not Received</option>
                  <option value="rogue_agent_injection">Rogue Agent Injection / Overspend</option>
                </select>
              </div>

              <div>
                <label className="block text-slate-600 font-medium mb-1">Disputed Amount (INR)</label>
                <input
                  type="number"
                  value={disputedAmount}
                  onChange={(e) => setDisputedAmount(Number(e.target.value))}
                  className="w-full border border-border/50 rounded p-1.5 text-xs bg-white font-mono"
                />
              </div>

              <div>
                <label className="block text-slate-600 font-medium mb-1">Claim Details</label>
                <textarea
                  rows={2}
                  value={details}
                  onChange={(e) => setDetails(e.target.value)}
                  className="w-full border border-border/50 rounded p-1.5 text-xs bg-white"
                />
              </div>

              <button
                disabled={evaluating || !selectedDetail}
                onClick={handleRunEvaluation}
                className="w-full mt-2 bg-primary hover:bg-primary/90 text-white font-semibold py-2 px-3 rounded text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-50"
              >
                <Sparkles size={14} />
                {evaluating ? 'Adjudicating Dispute...' : 'Run Liability Arbiter on Selected Obligation'}
              </button>
            </div>
          </Panel>
        </div>

        {/* Right: Dispute Evidence Package Inspector */}
        <div>
          {selectedDetail ? (
            <div className="space-y-4">
              {/* Verdict Summary Box */}
              <div
                className={`p-5 rounded-lg border shadow-sm ${
                  selectedDetail.liability_verdict.outcome === 'MERCHANT_PROTECTED'
                    ? 'bg-emerald-50/70 border-emerald-200'
                    : selectedDetail.liability_verdict.outcome === 'REFUND_ISSUED'
                    ? 'bg-amber-50/70 border-amber-200'
                    : 'bg-purple-50/70 border-purple-200'
                }`}
              >
                <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <ShieldCheck
                      className={
                        selectedDetail.liability_verdict.outcome === 'MERCHANT_PROTECTED'
                          ? 'text-emerald-700'
                          : 'text-amber-700'
                      }
                      size={24}
                    />
                    <div>
                      <div className="text-[11px] font-mono font-semibold uppercase text-slate-600">
                        Liability Arbiter Ruling
                      </div>
                      <div className="text-base font-bold text-slate-900">
                        {selectedDetail.liability_verdict.outcome}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-600">Fault:</span>
                    <span className="px-2 py-0.5 bg-white border rounded font-mono text-xs font-bold text-slate-800">
                      {selectedDetail.liability_verdict.assigned_fault}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">
                      ({Math.round(selectedDetail.liability_verdict.confidence * 100)}% conf)
                    </span>
                  </div>
                </div>

                <p className="text-xs text-slate-800 leading-relaxed font-sans bg-white/70 p-3 rounded border border-slate-200/50">
                  {selectedDetail.liability_verdict.explanation}
                </p>

                {/* Reason Codes */}
                <div className="mt-3 flex items-center gap-1.5 flex-wrap">
                  <span className="text-[11px] text-slate-500 font-mono">Reason Codes:</span>
                  {selectedDetail.liability_verdict.reason_codes.map((rc) => (
                    <span
                      key={rc}
                      className="px-2 py-0.5 bg-slate-900 text-white font-mono text-[10px] rounded"
                    >
                      {rc}
                    </span>
                  ))}
                </div>
              </div>

              {/* Consent Evidence Chain Box */}
              {selectedDetail.consent_record && (
                <Panel title="Cryptographic Consent Chain" meta="Human Delegation Proof">
                  <div className="p-4 space-y-2.5 text-xs">
                    <div className="grid grid-cols-2 gap-3 pb-2 border-b border-border/50">
                      <div>
                        <span className="text-slate-500 block text-[11px]">Consent ID</span>
                        <span className="font-mono font-semibold text-slate-800">
                          {selectedDetail.consent_record.consent_id}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 block text-[11px]">Principal Ref</span>
                        <span className="font-mono text-slate-800">
                          {selectedDetail.consent_record.principal_ref}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 block text-[11px]">Max Spend Bound</span>
                        <span className="font-mono font-semibold text-emerald-700">
                          ₹{(selectedDetail.consent_record.constraints.max_amount / 100).toLocaleString('en-IN')}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 block text-[11px]">Mandate Chain Hash</span>
                        <span className="font-mono text-[11px] text-slate-700 truncate block">
                          {selectedDetail.consent_record.mandate_chain_hash.substring(0, 24)}...
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-emerald-800 bg-emerald-50 px-2.5 py-1.5 rounded text-[11px]">
                      <UserCheck size={14} />
                      <span>
                        Ed25519 principal delegation signature verified against timestamp window.
                      </span>
                    </div>
                  </div>
                </Panel>
              )}

              {/* Settlement Certificate */}
              {selectedDetail.settlement_certificate && (
                <Panel title="Settlement Certificate" meta="Card Network Proof">
                  <div className="p-4 space-y-2.5 text-xs font-mono">
                    <div className="flex justify-between items-center bg-slate-900 text-white p-3 rounded">
                      <div>
                        <div className="text-[10px] text-slate-400 uppercase">Certificate ID</div>
                        <div className="text-xs font-bold text-indigo-300">
                          {selectedDetail.settlement_certificate.certificate_id}
                        </div>
                      </div>
                      <span className="px-2 py-0.5 bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 rounded text-[11px]">
                        {selectedDetail.settlement_certificate.performance_verdict} (Class: {selectedDetail.settlement_certificate.aggregate_basis})
                      </span>
                    </div>
                    <div className="space-y-1 text-[11px] text-slate-600 bg-slate-50 p-2.5 rounded border border-border/50">
                      <div><strong>Decision Hash:</strong> {selectedDetail.settlement_certificate.clearing_decision_hash.substring(0, 32)}...</div>
                      <div><strong>Certificate Hash:</strong> {selectedDetail.settlement_certificate.certificate_hash.substring(0, 32)}...</div>
                      <div><strong>Merchant Signature:</strong> Verified Ed25519</div>
                    </div>
                  </div>
                </Panel>
              )}

              {/* Visa CE3.0 Representment Brief */}
              <Panel title="Dispute Representment Package (Markdown Brief)" meta="Visa CE3.0 / MC Format">
                <div className="p-4">
                  <pre className="bg-slate-900 text-slate-200 p-4 rounded text-[11px] font-mono overflow-x-auto max-h-96 whitespace-pre-wrap leading-relaxed">
                    {selectedDetail.representment_brief_markdown}
                  </pre>
                </div>
              </Panel>
            </div>
          ) : (
            <div className="p-12 text-center text-muted-foreground/70 border border-dashed rounded-lg">
              Select a dispute from the left to view the complete evidence package.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
