'use client'

import { useEffect, useMemo, useState } from 'react'
import { usePathname } from 'next/navigation'
import { Bell, Menu } from 'lucide-react'

import { MobileSidebar, NAV_ITEMS, Sidebar } from '@/components/sidebar'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [lastUpdated, setLastUpdated] = useState('')

  useEffect(() => {
    const updateStamp = () => {
      setLastUpdated(
        new Date().toLocaleTimeString('en-US', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      )
    }

    updateStamp()
    const intervalId = window.setInterval(updateStamp, 1000)
    return () => window.clearInterval(intervalId)
  }, [])

  const activeLabel = useMemo(() => {
    const current = NAV_ITEMS.find((item) =>
      item.href === '/dashboard'
        ? pathname === item.href
        : pathname?.startsWith(item.href)
    )

    return current?.label ?? 'Operator'
  }, [pathname])

  return (
    <>
      <div className="botux-shell">
        <aside className="botux-shell__sidebar">
          <Sidebar />
        </aside>

        <div className="botux-shell__main">
          <header className="botux-topbar">
            <div className="botux-topbar__lead">
              <Button
                variant="outline"
                size="icon-sm"
                className="rounded-full lg:hidden"
                onClick={() => setMobileOpen(true)}
              >
                <Menu className="h-4 w-4" />
              </Button>

              <div className="botux-topbar__meta">
                <div>
                  <p className="botux-topbar__eyebrow">Operator Console</p>
                  <p className="botux-topbar__title">{activeLabel}</p>
                </div>
              </div>
            </div>

            <div className="botux-topbar__actions">
              <div className="hidden items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground md:flex">
                <span className="h-2 w-2 rounded-full bg-[var(--accent)] dot-pulse" />
                Live data
              </div>

              <div className="hidden items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 md:flex">
                <Bell className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="botux-topbar__stamp tabular-nums" suppressHydrationWarning>
                  {lastUpdated}
                </span>
              </div>

              <ThemeToggle />
            </div>
          </header>

          <main className="botux-shell__content">{children}</main>
        </div>
      </div>

      <MobileSidebar open={mobileOpen} onOpenChange={setMobileOpen} />
    </>
  )
}
