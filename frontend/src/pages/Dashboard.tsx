import { Link } from 'react-router-dom'
import { api } from '../api'
import type { DecisionSummary, MetricsResponse } from '../api'
import { useApi } from '../hooks/useApi'
import { Badge } from '../components/Badge'
import { StatGrid } from '../components/StatGrid'
import { Panel } from '../components/Panel'
import { Loader, ErrorBox } from '../components/Loader'

function useDashboard() {
  const metrics = useApi<MetricsResponse>(api.metrics)
  const decisions = useApi<DecisionSummary[]>(api.decisions)
  return { metrics, decisions }
}

export function Dashboard() {
  const { metrics, decisions } = useDashboard()

  if (metrics.loading || decisions.loading) return <Loader />
  if (metrics.error) return <ErrorBox message={metrics.error} />
  if (decisions.error) return <ErrorBox message={decisions.error} />
  if (!metrics.data || !decisions.data) return null

  const m = metrics.data

  return (
    <>
      <section className="flex justify-between gap-5 items-end mb-6 max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-muted-foreground/70 font-mono text-[11px] font-medium tracking-widest uppercase">
            Merchant control plane
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight">
            Agent transaction oversight
          </h1>
        </div>
        <div className="flex items-center gap-3 max-md:flex-wrap">
          <Link
            to="/dashboard/simulation"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 text-white rounded text-xs font-semibold no-underline transition-colors cursor-pointer"
          >
            Launch Live Simulation →
          </Link>
          <div
            className={`rounded-sm px-3 py-1.5 font-mono text-xs font-semibold whitespace-nowrap ${
              m.operations.ledger_ok
                ? 'bg-green-500/10 text-green-600'
                : 'bg-destructive/10 text-destructive'
            }`}
          >
            Ledger {m.operations.ledger_ok ? 'intact' : 'needs review'}
          </div>
        </div>
      </section>

      <StatGrid
        items={[
          { label: 'Recorded decisions', value: m.operations.decisions },
          { label: 'Review queue', value: m.operations.quarantined, warn: true },
          { label: 'Open obligations', value: m.operations.obligations },
          {
            label: 'Benchmark recall',
            value: `${Math.round(m.benchmark.recall * 100)}%`,
          },
        ]}
      />

      <div className="grid grid-cols-[minmax(0,2fr)_minmax(300px,0.85fr)] max-md:grid-cols-1 gap-5 mb-5">
        <Panel
          title="Decision log"
          meta={`${decisions.data.length} records`}
        >
          <div className="overflow-x-auto">
            <table className="border-collapse w-full min-w-[600px]">
              <thead>
                <tr>
                  {['Decision', 'Agent', 'Reason', 'Latency', ''].map((h) => (
                    <th
                      key={h}
                      className="px-4.5 py-3 text-left border-b border-border/50 bg-muted/30 text-muted-foreground/70 font-mono text-[10.5px] uppercase font-semibold tracking-wide"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {decisions.data.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="text-muted-foreground/70 text-center px-5 py-9 text-[13px]"
                    >
                      No agent purchases have run through the gateway yet.
                    </td>
                  </tr>
                ) : (
                  decisions.data.map((d) => (
                    <tr
                      key={d.decision_id}
                      className="transition-colors duration-150 hover:bg-[#f8f8fb] cursor-pointer"
                    >
                      <td className="px-4.5 py-3 text-[13px] border-b border-border/50">
                        <Badge value={d.decision} />
                      </td>
                      <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80">
                        {d.agent_id}
                      </td>
                      <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80">
                        {d.reason_codes.join(', ') || 'Verified'}
                      </td>
                      <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80 tabular-nums">
                        {d.latency_ms.toFixed(2)} ms
                      </td>
                      <td className="px-4.5 py-3 text-[13px] border-b border-border/50">
                        <Link
                          to={`/dashboard/decisions/${d.decision_id}`}
                          className="text-primary text-xs font-semibold no-underline hover:underline cursor-pointer"
                        >
                          Inspect
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <aside className="bg-card border border-border rounded-md overflow-hidden">
          <div className="min-h-[50px] px-4.5 py-3.5 flex items-center justify-between gap-4 border-b border-border/50">
            <h2 className="text-sm font-semibold m-0">Measured posture</h2>
            <Link
              to="/dashboard/metrics"
              className="text-primary text-xs font-semibold no-underline hover:underline cursor-pointer"
            >
              Details
            </Link>
          </div>
          <dl className="m-0 px-4.5 py-1 pb-4">
            {[
              ['Evaluation corpus', `${m.benchmark.sessions} sessions`],
              ['Attack classes', `${m.benchmark.attacks} attempts`],
              ['Precision', `${Math.round(m.benchmark.precision * 100)}%`],
              ['Inline p99', `${m.benchmark.p99.toFixed(2)} ms`],
              ['Chain entries', String(m.operations.ledger_entries)],
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
        </aside>
      </div>
    </>
  )
}
