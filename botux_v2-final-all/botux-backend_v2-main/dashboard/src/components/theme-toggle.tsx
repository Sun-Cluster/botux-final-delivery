'use client'

import { startTransition, useState } from 'react'
import { Moon, SunMedium } from 'lucide-react'

import { Button } from '@/components/ui/button'

type Theme = 'dark' | 'light'

const THEME_KEY = 'botux-theme'

function applyTheme(theme: Theme) {
  const root = document.documentElement
  root.classList.remove('light', 'dark')
  root.classList.add(theme)
  root.dataset.theme = theme
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof document === 'undefined') return 'dark'

    const rootTheme = document.documentElement.dataset.theme
    if (rootTheme === 'light' || rootTheme === 'dark') return rootTheme

    const stored = window.localStorage.getItem(THEME_KEY)
    return stored === 'light' ? 'light' : 'dark'
  })

  const toggleTheme = () => {
    const nextTheme: Theme = theme === 'dark' ? 'light' : 'dark'
    startTransition(() => {
      applyTheme(nextTheme)
      window.localStorage.setItem(THEME_KEY, nextTheme)
      setTheme(nextTheme)
    })
  }

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={toggleTheme}
      suppressHydrationWarning
      className="gap-2 rounded-full border-border bg-background px-3"
    >
      {theme === 'dark' ? <SunMedium className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      <span className="hidden sm:inline">{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
    </Button>
  )
}
