const styles: Record<string, string> = {
  allow: 'bg-allow-bg text-allow',
  final: 'bg-allow-bg text-allow',
  deny: 'bg-deny-bg text-deny',
  disputed: 'bg-deny-bg text-deny',
  quarantine: 'bg-quarantine-bg text-quarantine',
  step_up: 'bg-step-up-bg text-step-up',
  provisional: 'bg-step-up-bg text-step-up',
}

export function Badge({ value }: { value: string }) {
  const key = value.toLowerCase()
  const cls = styles[key] ?? 'bg-line-soft text-ink-soft'

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-1 font-mono text-[11px] font-semibold tracking-wide ${cls}`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
      {value}
    </span>
  )
}
