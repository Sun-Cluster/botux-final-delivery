'use client'

import { useEffect, useMemo, useState } from 'react'
import { Activity, CheckCircle2, KeyRound, RefreshCw, Save, ServerCrash } from 'lucide-react'

import { DashboardLoading } from '@/components/dashboard-loading'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  checkBrokerConnection,
  getSettings,
  type SettingsField,
  type SettingsSection,
  type SettingsTab,
  updateSettingConfig,
} from '@/lib/api'

type FormState = Record<string, string | number | boolean>
type BusyState = Record<string, boolean>
type NoticeState = Record<string, { kind: 'success' | 'error'; message: string }>
type ConnectionState = Record<string, any>

function buildFormState(tabs: SettingsTab[]): FormState {
  const next: FormState = {}
  for (const tab of tabs) {
    for (const section of tab.sections) {
      for (const field of section.fields) {
        next[field.key] = field.secret ? '' : field.value
      }
    }
  }
  return next
}

function normalizeFieldValue(field: SettingsField, raw: string | number | boolean) {
  if (field.value_type === 'bool') return Boolean(raw)
  if (field.value_type === 'int') return Number.parseInt(String(raw || '0'), 10) || 0
  if (field.value_type === 'float') return Number.parseFloat(String(raw || '0')) || 0
  return String(raw ?? '')
}

export default function SettingsPage() {
  const [tabs, setTabs] = useState<SettingsTab[] | null>(null)
  const [form, setForm] = useState<FormState>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<BusyState>({})
  const [checking, setChecking] = useState<BusyState>({})
  const [notice, setNotice] = useState<NoticeState>({})
  const [connections, setConnections] = useState<ConnectionState>({})

  async function refreshBrokerConnections(nextTabs: SettingsTab[]) {
    const brokerNames = nextTabs
      .flatMap((tab) => tab.sections)
      .map((section) => section.broker_name)
      .filter((brokerName): brokerName is string => Boolean(brokerName))

    if (brokerNames.length === 0) return

    setChecking((current) => {
      const next = { ...current }
      for (const brokerName of brokerNames) next[brokerName] = true
      return next
    })

    const results = await Promise.all(
      brokerNames.map(async (brokerName) => ({
        brokerName,
        payload: await checkBrokerConnection(brokerName),
      }))
    )

    setConnections((current) => {
      const next = { ...current }
      for (const { brokerName, payload } of results) {
        if (payload) next[brokerName] = payload
      }
      return next
    })

    setChecking((current) => {
      const next = { ...current }
      for (const brokerName of brokerNames) next[brokerName] = false
      return next
    })
  }

  async function loadSettings() {
    const payload = await getSettings()
    if (!payload?.tabs) {
      setTabs([])
      setLoading(false)
      return
    }
    setTabs(payload.tabs)
    setForm(buildFormState(payload.tabs))
    const nextConnections: ConnectionState = {}
    for (const tab of payload.tabs) {
      for (const section of tab.sections) {
        if (section.broker_name && section.connection) {
          nextConnections[section.broker_name] = section.connection
        }
      }
    }
    setConnections(nextConnections)
    setLoading(false)
    void refreshBrokerConnections(payload.tabs)
  }

  useEffect(() => {
    loadSettings()
  }, [])

  const tabOrder = useMemo(() => tabs ?? [], [tabs])

  async function saveSection(section: SettingsSection) {
    setSaving((current) => ({ ...current, [section.id]: true }))
    setNotice((current) => {
      const next = { ...current }
      delete next[section.id]
      return next
    })
    try {
      for (const field of section.fields) {
        const result = await updateSettingConfig(field.key, {
          value: normalizeFieldValue(field, form[field.key]),
          updated_by: 'dashboard.settings',
        })
        if (!result) {
          throw new Error(`save failed for ${field.key}`)
        }
      }
      await loadSettings()
      setNotice((current) => ({
        ...current,
        [section.id]: { kind: 'success', message: 'Saved to runtime config.' },
      }))
    } catch {
      setNotice((current) => ({
        ...current,
        [section.id]: { kind: 'error', message: 'Save failed. Please retry.' },
      }))
    } finally {
      setSaving((current) => ({ ...current, [section.id]: false }))
    }
  }

  async function runConnectionCheck(section: SettingsSection) {
    if (!section.broker_name) return
    setChecking((current) => ({ ...current, [section.broker_name!]: true }))
    const payload = await checkBrokerConnection(section.broker_name)
    if (payload) {
      setConnections((current) => ({ ...current, [section.broker_name!]: payload }))
      setNotice((current) => ({
        ...current,
        [section.id]: {
          kind: payload.connected ? 'success' : 'error',
          message: payload.connected ? 'Connection OK.' : (payload.error || 'Connection failed.'),
        },
      }))
    } else {
      setNotice((current) => ({
        ...current,
        [section.id]: { kind: 'error', message: 'Could not reach broker check endpoint.' },
      }))
    }
    setChecking((current) => ({ ...current, [section.broker_name!]: false }))
  }

  if (loading) {
    return <DashboardLoading title="Loading settings" description="Reading broker, execution, and data-source runtime config." />
  }

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Operations</div>
          <h1 className="botux-page__title">Settings</h1>
          <p className="botux-page__description">
            Broker accounts and platform runtime controls live here. Bot-specific config stays out of this page for now.
          </p>
        </div>
      </section>

      <Tabs defaultValue={tabOrder[0]?.id ?? 'brokers'} className="gap-5">
        <TabsList>
          {tabOrder.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {tabOrder.map((tab) => (
          <TabsContent key={tab.id} value={tab.id} className="space-y-5">
            {tab.sections.map((section) => {
              const connection = section.broker_name ? connections[section.broker_name] : null
              const isSaving = Boolean(saving[section.id])
              const isChecking = section.broker_name ? Boolean(checking[section.broker_name]) : false
              const sectionNotice = notice[section.id]

              return (
                <Card key={section.id} className="botux-panel-card overflow-hidden">
                  <CardHeader className="border-b border-border/60 pb-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="space-y-1">
                        <CardTitle className="text-lg">{section.title}</CardTitle>
                        <p className="text-sm text-muted-foreground">{section.description}</p>
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        {connection ? (
                          <>
                            <Badge
                              variant="outline"
                              className={
                                isChecking
                                  ? 'border-sky-500/30 bg-sky-500/10 text-sky-300'
                                  : connection.connected
                                    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                                    : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                              }
                            >
                              {isChecking ? 'Checking...' : connection.connected ? 'Connected' : 'Disconnected'}
                            </Badge>
                            <Badge variant="outline" className="border-border/80 bg-background/60">
                              {String(connection.mode || 'paper').toUpperCase()}
                            </Badge>
                          </>
                        ) : null}

                        {section.broker_name ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={isChecking || isSaving}
                            onClick={() => runConnectionCheck(section)}
                          >
                            {isChecking ? <RefreshCw className="animate-spin" /> : <Activity />}
                            Check Connect
                          </Button>
                        ) : null}
                        <Button
                          size="sm"
                          className="bg-[var(--accent)] text-black hover:bg-[var(--accent-strong)]"
                          disabled={isSaving || isChecking}
                          onClick={() => saveSection(section)}
                        >
                          {isSaving ? <RefreshCw className="animate-spin" /> : <Save />}
                          Save Config
                        </Button>
                      </div>
                    </div>
                  </CardHeader>

                  <CardContent className="space-y-4 pt-5">
                    {connection ? (
                      <div className="grid gap-3 md:grid-cols-4">
                        <StatusBlock
                          label="Status"
                          value={isChecking ? 'checking' : String(connection.status || 'unknown')}
                          ok={Boolean(connection.connected)}
                        />
                        <StatusBlock label="Account" value={connection.account_number || '—'} />
                        <StatusBlock label="Configured" value={connection.configured ? 'Yes' : 'No'} ok={Boolean(connection.configured)} />
                        <StatusBlock label="Error" value={connection.error || '—'} danger={Boolean(connection.error)} />
                      </div>
                    ) : null}

                    <div className="grid gap-4 md:grid-cols-2">
                      {section.fields.map((field) => (
                        <FieldEditor
                          key={field.key}
                          field={field}
                          value={form[field.key]}
                          onChange={(value) =>
                            setForm((current) => ({
                              ...current,
                              [field.key]: value,
                            }))
                          }
                        />
                      ))}
                    </div>

                    {sectionNotice ? (
                      <div
                        className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                          sectionNotice.kind === 'success'
                            ? 'bg-emerald-500/10 text-emerald-300'
                            : 'bg-rose-500/10 text-rose-300'
                        }`}
                      >
                        {sectionNotice.kind === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <ServerCrash className="h-4 w-4" />}
                        <span>{sectionNotice.message}</span>
                      </div>
                    ) : null}
                  </CardContent>
                </Card>
              )
            })}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function FieldEditor({
  field,
  value,
  onChange,
}: {
  field: SettingsField
  value: string | number | boolean
  onChange: (value: string | number | boolean) => void
}) {
  return (
    <div className="space-y-2 rounded-lg border border-border/60 bg-background/35 p-4">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold">{field.label}</p>
          {field.secret ? <KeyRound className="h-3.5 w-3.5 text-amber-400" /> : null}
        </div>
        <p className="text-xs text-muted-foreground">{field.description}</p>
      </div>

      {field.value_type === 'bool' ? (
        <div className="flex min-h-9 items-center justify-between rounded-lg border border-border/60 bg-[var(--surface-elevated)] px-3">
          <span className="text-xs text-muted-foreground">Toggle setting</span>
          <Switch checked={Boolean(value)} onCheckedChange={onChange} />
        </div>
      ) : (
        <Input
          type={field.secret ? 'password' : field.value_type === 'str' ? 'text' : 'number'}
          value={String(value ?? '')}
          placeholder={field.secret ? field.display_value || 'Enter new value' : field.label}
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {field.secret ? (
        <p className="text-[11px] text-muted-foreground">
          Leave blank to keep the existing secret value.
        </p>
      ) : null}
    </div>
  )
}

function StatusBlock({
  label,
  value,
  ok = false,
  danger = false,
}: {
  label: string
  value: string
  ok?: boolean
  danger?: boolean
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/30 p-3">
      <p className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${ok ? 'text-emerald-300' : danger ? 'text-rose-300' : ''}`}>
        {value}
      </p>
    </div>
  )
}
