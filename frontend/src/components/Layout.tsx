import { NavLink, Outlet } from 'react-router-dom'
import { ExternalLink, ShieldCheck, Activity, Scale, CheckSquare, BarChart, ShoppingBag } from 'lucide-react'

const navItems = [
  { to: '/store', label: 'Storefront', icon: ShoppingBag },
  { to: '/dashboard', label: 'Overview', end: true, icon: Activity },
  { to: '/dashboard/simulation', label: 'Simulation', icon: ShieldCheck },
  { to: '/dashboard/disputes', label: 'Disputes', icon: Scale },
  { to: '/dashboard/quarantine', label: 'Review Queue', icon: CheckSquare },
  { to: '/dashboard/metrics', label: 'Benchmark', icon: BarChart },
]

export function Layout() {
  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
          
          {/* Logo Section */}
          <div className="flex items-center gap-6">
            <NavLink
              to="/store"
              className="flex items-center gap-2.5 font-semibold text-lg tracking-tight no-underline group"
            >
              <div className="w-8 h-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center font-bold text-sm shadow-sm group-hover:scale-105 transition-transform">
                K
              </div>
              <span className="hidden sm:inline-block">Know Your Agent</span>
            </NavLink>
            
            <div className="hidden lg:block h-6 w-px bg-border"></div>

            {/* Main Navigation */}
            <nav className="hidden lg:flex items-center gap-1" aria-label="Primary navigation">
              {navItems.map(({ to, label, end, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `px-3 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2 ${
                      isActive
                        ? 'bg-secondary text-secondary-foreground'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`
                  }
                >
                  <Icon className="w-4 h-4" />
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>

          {/* Right Section (Badges & Links) */}
          <div className="flex items-center gap-3 ml-auto">
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="hidden md:flex text-sm font-medium text-muted-foreground hover:text-foreground transition-colors items-center gap-1.5 px-3 py-2"
            >
              API Docs
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
            
            <div className="hidden sm:flex items-center gap-2">
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400 text-[11px] font-semibold tracking-wide uppercase">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                Test Rails
              </div>
              <div className="px-2.5 py-1 rounded-full border border-border bg-muted/50 text-muted-foreground text-[11px] font-semibold tracking-wide uppercase">
                Sandbox
              </div>
            </div>
          </div>
        </div>
        
        {/* Mobile Navigation Scroll Area */}
        <div className="lg:hidden border-t border-border bg-background">
          <nav className="flex items-center gap-1 overflow-x-auto px-4 py-2 no-scrollbar" aria-label="Mobile navigation">
            {navItems.map(({ to, label, end, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 whitespace-nowrap ${
                    isActive
                      ? 'bg-secondary text-secondary-foreground'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`
                }
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>
    </div>
  )
}
