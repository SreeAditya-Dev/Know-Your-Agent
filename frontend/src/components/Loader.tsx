import { Loader2 } from 'lucide-react'

export function Loader({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-ink-faint text-sm">
      <Loader2 size={18} className="animate-spin" />
      {label}
    </div>
  )
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-deny-bg text-deny rounded-md px-4.5 py-3 text-sm font-medium">
      {message}
    </div>
  )
}
