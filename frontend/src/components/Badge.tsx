const styles: Record<string, string> = {
  allow: 'bg-green-500/10 text-green-600',
  final: 'bg-green-500/10 text-green-600',
  deny: 'bg-destructive/10 text-destructive',
  disputed: 'bg-destructive/10 text-destructive',
  quarantine: 'bg-orange-500/10 text-orange-500',
  step_up: 'bg-blue-500/10 text-blue-600',
  provisional: 'bg-blue-500/10 text-blue-600',
}

export function Badge({ value }: { value: string }) {
  const key = value.toLowerCase()
  const cls = styles[key] ?? 'bg-line-soft text-muted-foreground'

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-1 font-mono text-[11px] font-semibold tracking-wide ${cls}`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
      {value}
    </span>
  )
}
