import { NavLink, Outlet } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'

const navItems = [
  { to: '/dashboard', label: 'Overview', end: true },
  { to: '/dashboard/quarantine', label: 'Review queue' },
  { to: '/dashboard/metrics', label: 'Benchmark' },
]

export function Layout() {
  return (
    <>
      <header className="min-h-14 flex items-center gap-7 px-7 bg-chrome border-b border-chrome-line text-paper max-md:flex-wrap max-md:gap-3.5 max-md:px-4 max-md:py-3">
        <NavLink
          to="/dashboard"
          className="flex items-baseline gap-1.5 font-mono text-[15px] font-semibold tracking-wide no-underline whitespace-nowrap text-paper"
        >
          KYA<span className="text-[#a89af0] font-medium">/control</span>
        </NavLink>

        <nav className="flex gap-5.5 items-center flex-1 max-md:order-3 max-md:basis-full max-md:overflow-x-auto max-md:pb-0.5" aria-label="Primary navigation">
          {navItems.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `text-[13px] font-medium no-underline py-1 border-b transition-colors duration-150 cursor-pointer ${
                  isActive
                    ? 'text-paper border-signal'
                    : 'text-chrome-text border-transparent hover:text-paper'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="text-[13px] font-medium text-chrome-text no-underline py-1 border-b border-transparent hover:text-paper transition-colors duration-150 inline-flex items-center gap-1 cursor-pointer"
          >
            API docs
            <ExternalLink size={12} />
          </a>
        </nav>

        <div className="border border-chrome-line rounded-sm px-2.5 py-1 text-chrome-dim font-mono text-[11px] font-medium tracking-widest uppercase whitespace-nowrap max-md:ml-auto">
          Sandbox mode
        </div>
      </header>

      <main className="max-w-[1360px] mx-auto px-7 py-8 pb-14 max-md:px-4 max-md:py-6 max-md:pb-10 w-full animate-[rise_220ms_ease-out]">
        <Outlet />
      </main>
    </>
  )
}
