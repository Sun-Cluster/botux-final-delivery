'use client'

import Image from 'next/image'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Activity,
  BarChart3,
  Bot,
  LayoutDashboard,
  MessageSquare,
  Plane,
  Radio,
  Settings2,
  Shield,
  TrendingUp,
  Users,
} from 'lucide-react'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'

export const NAV_ITEMS = [
  { label: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { label: 'My Bots', href: '/dashboard/bots', icon: Bot },
  { label: 'Portfolio', href: '/dashboard/portfolio', icon: TrendingUp },
  { label: 'Trades', href: '/dashboard/trades', icon: BarChart3 },
  { label: 'Signals', href: '/dashboard/signals', icon: Radio },
  { label: 'Agents', href: '/dashboard/agents', icon: Users },
  { label: 'Autopilot', href: '/dashboard/autopilot', icon: Plane },
  { label: 'Chat', href: '/dashboard/chat', icon: MessageSquare },
  { label: 'Risk', href: '/dashboard/risk', icon: Shield },
  { label: 'Monitoring', href: '/dashboard/monitoring', icon: Activity },
  { label: 'Settings', href: '/dashboard/settings', icon: Settings2 },
]

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()

  return (
    <div className="botux-sidebar">
      <div className="botux-sidebar__brand">
        <div className="botux-sidebar__logo">
          <Image src="/logo.png" alt="BOTUX" width={32} height={32} className="rounded-xl" />
        </div>
        <div>
          <div className="botux-sidebar__title">
            <span>BOTU</span>
            <span className="text-[var(--accent)]">X</span>
          </div>
          <p className="botux-sidebar__caption">AI Trading Platform</p>
        </div>
      </div>

      <nav className="botux-sidebar__nav">
        {NAV_ITEMS.map((item) => {
          const active =
            pathname === item.href ||
            (item.href !== '/dashboard' && pathname?.startsWith(item.href))

          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`botux-sidebar__link ${active ? 'is-active' : ''}`}
            >
              {active ? <span className="botux-sidebar__link-mark" /> : null}
              <item.icon className="h-4 w-4 shrink-0" />
              <span className="font-medium">{item.label}</span>
            </Link>
          )
        })}
      </nav>

      <div className="botux-sidebar__footer">
        <div className="botux-sidebar__status">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-[var(--accent)] dot-pulse" />
            <div>
              <p className="botux-sidebar__status-label">System status</p>
              <p className="botux-sidebar__status-value">Live operator online</p>
            </div>
          </div>
          <span className="rounded-full bg-[rgba(var(--accent-rgb),0.14)] px-2 py-1 text-[10px] font-semibold text-[var(--accent)]">
            Preview
          </span>
        </div>

        <div className="botux-sidebar__version">
          <span>BOTUX v2.26</span>
          <span>US + ASX</span>
        </div>
      </div>
    </div>
  )
}

export function Sidebar() {
  return <SidebarContent />
}

export function MobileSidebar({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        className="w-[18rem] border-r border-border bg-transparent p-0 shadow-none [&>button]:hidden"
      >
        <SheetHeader className="sr-only">
          <SheetTitle>Navigation</SheetTitle>
          <SheetDescription>Navigate between dashboard sections.</SheetDescription>
        </SheetHeader>
        <SidebarContent onNavigate={() => onOpenChange(false)} />
      </SheetContent>
    </Sheet>
  )
}
