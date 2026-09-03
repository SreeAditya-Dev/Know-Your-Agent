import { Link } from 'react-router-dom'
import { api } from '../api'
import type { DecisionSummary } from '../api'
import { useApi } from '../hooks/useApi'
import { Loader, ErrorBox } from '../components/Loader'

export function QuarantinePage() {
  const { data, loading, error } = useApi<DecisionSummary[]>(api.decisions)

  if (loading) return <Loader />
  if (error) return <ErrorBox message={error} />

  const quarantined = (data ?? []).filter(
    (d) => d.decision === 'QUARANTINE',
  )

  return (
    <>
      <section className="flex justify-between gap-5 items-end mb-6 max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-muted-foreground/70 font-mono text-[11px] font-medium tracking-widest uppercase">
            Human review
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight">
            Quarantine queue
          </h1>
        </div>
        <div className="rounded-sm px-3 py-1.5 font-mono text-xs font-semibold whitespace-nowrap bg-orange-500/10 text-orange-500">
          {quarantined.length} pending
        </div>
      </section>

      <section className="bg-card border border-border rounded-md overflow-hidden">
        <div className="overflow-x-auto">
          <table className="border-collapse w-full min-w-[600px]">
            <thead>
              <tr>
                {['Decision', 'Agent', 'Reason codes', 'Explanation', ''].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-4.5 py-3 text-left border-b border-border/50 bg-muted/30 text-muted-foreground/70 font-mono text-[10.5px] uppercase font-semibold tracking-wide"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {quarantined.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="text-muted-foreground/70 text-center px-5 py-9 text-[13px]"
                  >
                    Nothing currently requires review.
                  </td>
                </tr>
              ) : (
                quarantined.map((d) => (
                  <tr
                    key={d.decision_id}
                    className="transition-colors duration-150 hover:bg-[#f8f8fb] cursor-pointer"
                  >
                    <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80">
                      {d.decision_id}
                    </td>
                    <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80">
                      {d.agent_id}
                    </td>
                    <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80">
                      {d.reason_codes.join(', ')}
                    </td>
                    <td className="px-4.5 py-3 text-[13px] border-b border-border/50 text-foreground/80">
                      {d.explanation}
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
      </section>
    </>
  )
}
