import type { GateResult } from '../api'

const dotColor: Record<string, string> = {
  pass: 'border-allow bg-allow',
  fail: 'border-deny bg-deny',
  quarantine: 'border-quarantine bg-quarantine',
  skipped: 'border-ink-faint bg-paper',
  degraded: 'border-ink-faint bg-paper',
  unknown: 'border-step-up bg-step-up',
}

const textColor: Record<string, string> = {
  pass: 'text-allow',
  fail: 'text-deny',
  quarantine: 'text-quarantine',
  skipped: 'text-ink-faint',
  degraded: 'text-ink-faint',
  unknown: 'text-step-up',
}

export function GateTrace({ trace }: { trace: GateResult[] }) {
  return (
    <ol
      className="list-none m-0 p-4.5 flex flex-wrap gap-0 max-md:flex-col max-md:gap-3.5"
      aria-label="Inline pipeline trace for this decision"
    >
      {trace.map((g, i) => {
        const v = g.verdict.toLowerCase()
        const isDim = v === 'skipped' || v === 'degraded'
        return (
          <li
            key={i}
            className={`flex flex-col items-center gap-1.5 relative px-4 min-w-[84px] text-center max-md:flex-row max-md:justify-start max-md:text-left max-md:px-0 max-md:min-w-0`}
          >
            {i < trace.length - 1 && (
              <span
                className={`absolute top-[7px] left-[calc(50%+22px)] h-px max-md:hidden ${
                  isDim
                    ? 'border-t border-dashed border-line bg-transparent'
                    : 'bg-line'
                }`}
                style={{ width: 'calc(100% - 22px)' }}
                aria-hidden
              />
            )}
            <span
              className={`w-[15px] h-[15px] rounded-full border-2 shrink-0 ${
                dotColor[v] ?? 'border-line bg-paper'
              }`}
              aria-hidden
            />
            <span
              className={`font-mono text-xs font-semibold ${
                isDim ? 'text-ink-faint' : 'text-ink'
              }`}
            >
              {g.gate}
            </span>
            <span
              className={`font-mono text-[10px] font-semibold tracking-wide uppercase ${
                textColor[v] ?? 'text-ink-soft'
              }`}
            >
              {g.verdict}
            </span>
            {g.codes.length > 0 && (
              <span className="font-mono text-[10.5px] text-deny">
                {g.codes.join(', ')}
              </span>
            )}
            <span className="font-mono text-[10px] text-ink-faint">
              {g.elapsed_ms.toFixed(2)} ms
            </span>
          </li>
        )
      })}
    </ol>
  )
}
