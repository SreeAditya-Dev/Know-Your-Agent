import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  meta?: ReactNode
  children: ReactNode
  className?: string
}

export function Panel({ title, meta, children, className = '' }: PanelProps) {
  return (
    <section className={`bg-card border border-border rounded-md overflow-hidden ${className}`}>
      <div className="min-h-[50px] px-4.5 py-3.5 flex items-center justify-between gap-4 border-b border-border/50">
        <h2 className="text-sm font-semibold m-0">{title}</h2>
        {meta && (
          <span className="text-muted-foreground/70 font-mono text-[11px]">{meta}</span>
        )}
      </div>
      {children}
    </section>
  )
}
