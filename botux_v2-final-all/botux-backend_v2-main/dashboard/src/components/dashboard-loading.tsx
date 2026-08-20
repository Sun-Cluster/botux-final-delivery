"use client"

import { Activity } from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"

type DashboardLoadingProps = {
  title?: string
  description?: string
}

export function DashboardLoading({
  title = "Loading dashboard",
  description = "Fetching the latest broker, signal, and execution state.",
}: DashboardLoadingProps) {
  return (
    <div className="botux-page">
      <div className="flex min-h-[320px] items-center justify-center">
        <Card className="w-full max-w-md botux-panel-card">
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <Activity className="h-8 w-8 animate-spin text-muted-foreground" />
            <div className="space-y-1">
              <p className="text-sm font-semibold">{title}</p>
              <p className="text-xs text-muted-foreground">{description}</p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
