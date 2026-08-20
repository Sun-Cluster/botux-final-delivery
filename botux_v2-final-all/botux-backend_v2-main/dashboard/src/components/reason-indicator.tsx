"use client"

import { Info } from "lucide-react"

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

type ReasonIndicatorProps = {
  reason: string
}

export function ReasonIndicator({ reason }: ReasonIndicatorProps) {
  const content = reason.trim()
  if (!content) return null

  return (
    <TooltipProvider delay={120}>
      <Tooltip>
        <TooltipTrigger
          aria-label="View failure or rejection reason"
          className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-amber-500/35 bg-amber-500/10 text-amber-400 transition-colors hover:border-amber-400/60 hover:bg-amber-500/15 hover:text-amber-300"
        >
          <Info className="h-3.5 w-3.5" />
        </TooltipTrigger>
        <TooltipContent className="max-w-sm whitespace-pre-wrap leading-relaxed">
          {content}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
