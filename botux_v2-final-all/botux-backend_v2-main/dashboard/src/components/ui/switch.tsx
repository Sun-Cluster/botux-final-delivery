'use client'

import * as React from 'react'

import { cn } from '@/lib/utils'

type SwitchProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> & {
  checked?: boolean
  onCheckedChange?: (checked: boolean) => void
}

function Switch({ className, checked = false, disabled, onCheckedChange, ...props }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      data-state={checked ? 'checked' : 'unchecked'}
      className={cn(
        'peer inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-all outline-none',
        'focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked
          ? 'border-emerald-400/70 bg-emerald-500 shadow-[0_0_0_1px_rgba(16,185,129,0.15)]'
          : 'border-border/80 bg-muted/80 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04)]',
        className
      )}
      onClick={() => {
        if (disabled) return
        onCheckedChange?.(!checked)
      }}
      {...props}
    >
      <span
        className={cn(
          'pointer-events-none block h-5 w-5 rounded-full border shadow-sm ring-0 transition-transform',
          checked
            ? 'border-emerald-100/80 bg-white'
            : 'border-white/10 bg-slate-100 dark:bg-slate-50',
          checked ? 'translate-x-5' : 'translate-x-0.5'
        )}
      />
    </button>
  )
}

export { Switch }
