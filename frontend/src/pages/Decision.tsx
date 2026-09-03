import { useParams, Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { DecisionDetail } from '../api'
import { Badge } from '../components/Badge'
import { Panel } from '../components/Panel'
import { GateTrace } from '../components/GateTrace'
import { Loader, ErrorBox } from '../components/Loader'
import { ArrowLeft } from 'lucide-react'

export function DecisionPage() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<DecisionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    api
      .decision(id)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <Loader />
  if (error) return <ErrorBox message={error} />
  if (!data) return null

  const d = data.decision

  return (
    <>
      <Link
        to="/dashboard"
        className="inline-flex items-center gap-1.5 text-primary text-xs font-semibold no-underline hover:underline mb-4 cursor-pointer"
      >
        <ArrowLeft size={14} />
        Back to overview
      </Link>

      <section className="flex justify-between gap-5 items-center mb-6 max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-muted-foreground/70 font-mono text-[11px] font-medium tracking-widest uppercase">
            Decision record
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight font-mono">
            {d.decision_id}
          </h1>
        </div>
        <Badge value={d.decision} />
      </section>

      <div className="grid grid-cols-2 max-md:grid-cols-1 gap-5">
        <Panel title="Decision basis">
          <dl className="m-0 px-4.5 py-1 pb-4">
            {[
              ['Agent', d.agent_id],
              ['Tier', d.tier],
              ['Reason codes', d.reason_codes.join(', ') || 'Verified'],
              ['Obligation', d.obligation_id ?? 'Not minted'],
              ['Latency', `${d.latency_ms.toFixed(2)} ms`],
              ['Policy', d.policy_version],
            ].map(([dt, dd], i, arr) => (
              <div
                key={dt}
                className={`flex justify-between gap-4 py-3 ${
                  i < arr.length - 1 ? 'border-b border-border/50' : ''
                }`}
              >
                <dt className="text-muted-foreground text-[13px]">{dt}</dt>
                <dd className="m-0 text-foreground font-mono text-[13px] font-medium text-right break-words">
                  {dd}
                </dd>
              </div>
            ))}
          </dl>
          {d.explanation && (
            <p className="px-4.5 pb-4.5 mt-0 mb-0 text-[#3c4552] text-[13.5px] leading-relaxed">
              {d.explanation}
            </p>
          )}
        </Panel>

        <Panel
          title="Gate trace"
          meta="inline pipeline, in evaluation order"
        >
          <GateTrace trace={d.gate_trace} />
        </Panel>
      </div>
    </>
  )
}
