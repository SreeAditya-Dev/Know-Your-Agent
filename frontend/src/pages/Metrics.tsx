import { api } from '../api'
import type { BenchmarkResponse } from '../api'
import { useApi } from '../hooks/useApi'
import { StatGrid } from '../components/StatGrid'
import { Panel } from '../components/Panel'
import { Loader, ErrorBox } from '../components/Loader'

const LABELS: Record<string, string> = {
  B0: 'no gateway',
  B1: 'identity-only',
  B2: '+ mandate',
  B3: 'full KYA',
}

export function MetricsPage() {
  const { data, loading, error } = useApi<BenchmarkResponse>(api.benchmark)

  if (loading) return <Loader />
  if (error) return <ErrorBox message={error} />
  if (!data) return null

  const b3 = data.baselines['B3']

  return (
    <>
      <section className="flex justify-between gap-5 items-end mb-6 max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-muted-foreground/70 font-mono text-[11px] font-medium tracking-widest uppercase">
            Frozen red-team corpus
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight">
            Benchmark evidence
          </h1>
        </div>
        <div className="rounded-sm px-3 py-1.5 font-mono text-xs font-semibold whitespace-nowrap bg-green-500/10 text-green-600">
          {data.n_sessions} verified sessions
        </div>
      </section>

      {b3 && (
        <StatGrid
          items={[
            { label: 'B3 recall', value: `${Math.round(b3.recall * 100)}%` },
            { label: 'B3 precision', value: `${Math.round(b3.precision * 100)}%` },
            { label: 'False-positive rate', value: `${(b3.fpr * 100).toFixed(1)}%` },
            { label: 'Inline p99', value: `${b3.latency_p99.toFixed(2)} ms` },
          ]}
        />
      )}

      <Panel
        title="Defence posture comparison"
        meta={`Frozen corpus hash ${data.corpus_hash.slice(0, 12)}`}
      >
        <div className="overflow-x-auto">
          <table className="border-collapse w-full min-w-[600px]">
            <thead>
              <tr>
                {['Posture', 'Attacks stopped', 'Recall', 'Precision', 'FPR'].map(
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
              {Object.entries(data.baselines).map(([key, item]) => (
                <tr
                  key={key}
                  className="transition-colors duration-150 hover:bg-[#f8f8fb]"
                >
                  <td className="px-4.5 py-3 text-[13px] border-b border-border/50">
                    <span className="font-mono font-medium">{key}</span>{' '}
                    <span className="text-muted-foreground">{LABELS[key] ?? ''}</span>
                  </td>
                  <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80 tabular-nums">
                    {item.tp}/{item.tp + item.fn}
                  </td>
                  <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80 tabular-nums">
                    {Math.round(item.recall * 100)}%
                  </td>
                  <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80 tabular-nums">
                    {Math.round(item.precision * 100)}%
                  </td>
                  <td className="px-4.5 py-3 text-[13px] border-b border-border/50 font-mono text-foreground/80 tabular-nums">
                    {(item.fpr * 100).toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}
