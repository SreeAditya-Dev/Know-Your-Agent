interface Stat {
  label: string
  value: string | number
  warn?: boolean
}

export function StatGrid({ items }: { items: Stat[] }) {
  return (
    <section
      className="grid grid-cols-4 max-md:grid-cols-1 gap-px mb-5 bg-line border border-border rounded-md overflow-hidden"
      aria-label="Operational summary"
    >
      {items.map((s) => (
        <div
          key={s.label}
          className="bg-card min-h-24 max-md:min-h-22 px-4.5 py-4 flex flex-col justify-between"
        >
          <span className="text-muted-foreground text-xs font-medium">{s.label}</span>
          <strong
            className={`font-mono text-[25px] font-semibold tracking-tight ${
              s.warn ? 'text-orange-500' : 'text-foreground'
            }`}
          >
            {s.value}
          </strong>
        </div>
      ))}
    </section>
  )
}
