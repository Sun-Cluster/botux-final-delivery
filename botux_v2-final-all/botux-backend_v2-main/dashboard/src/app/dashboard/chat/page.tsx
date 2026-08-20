'use client'
import { useEffect, useRef, useState } from 'react'
import { postChat, getChatHistory } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { MessageSquare, Send, Bot, User as UserIcon, Clock, Zap, AlertCircle } from 'lucide-react'

type Msg = {
  role: 'user' | 'assistant'
  content: string
  ts: string
  meta?: { tokens?: number; ms?: number; model?: string }
}

const QUICK_PROMPTS = [
  "Give me a 1-paragraph summary of the system right now",
  "How many positions are open and what's the unrealized P&L?",
  "What has the council done in the last week?",
  "Which bot has the most trades and what's its win rate?",
  "Are any signals being blocked? What's the drop-off?",
  "What has Hermes learned recently?",
  "Show me the latest tuning adjustment and what it changed",
]

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadedHistory, setLoadedHistory] = useState(false)
  const feedRef = useRef<HTMLDivElement>(null)

  // Load prior history once
  useEffect(() => {
    async function load() {
      try {
        const h = await getChatHistory(20)
        if (h && h.history) {
          const flat: Msg[] = []
          for (const row of h.history) {
            flat.push({ role: 'user', content: row.user_message, ts: row.ts })
            flat.push({
              role: 'assistant',
              content: row.system_response,
              ts: row.ts,
              meta: { tokens: row.context_size_tokens_est, ms: row.response_time_ms, model: row.model_used }
            })
          }
          setMessages(flat)
        }
      } catch {}
      setLoadedHistory(true)
    }
    load()
  }, [])

  // Auto-scroll to bottom on new message
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight
  }, [messages])

  async function send(text?: string) {
    const msg = (text ?? input).trim()
    if (!msg || sending) return
    setError(null)
    setInput('')
    const userMsg: Msg = { role: 'user', content: msg, ts: new Date().toISOString() }
    setMessages(m => [...m, userMsg])
    setSending(true)
    try {
      const res = await postChat(msg)
      if (res?.error) {
        setError(res.error)
      } else {
        setMessages(m => [...m, {
          role: 'assistant',
          content: res?.response || '(empty response)',
          ts: new Date().toISOString(),
          meta: {
            tokens: res?.context_tokens_est,
            ms: res?.response_time_ms,
            model: res?.model || 'gpt-5.4',
          }
        }])
      }
    } catch (e: any) {
      setError(e?.message || 'request failed')
    }
    setSending(false)
  }

  return (
    <div className="botux-page h-[calc(100vh-7rem)]">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">
            <MessageSquare className="h-4 w-4" />
            Operator assistant
          </div>
          <h1 className="botux-page__title">BOTUX Operator Chat</h1>
          <p className="botux-page__description">
            Ask about live system state, positions, signals, council decisions, or learning. Read-only; no trades execute from here.
          </p>
        </div>
      </section>

      {/* Quick prompt chips */}
      <div className="flex flex-wrap gap-2">
        {QUICK_PROMPTS.map((q, i) => (
          <button
            key={i}
            disabled={sending}
            onClick={() => send(q)}
            className="text-[10px] px-2.5 py-1.5 rounded-full border border-border bg-background hover:bg-muted transition-colors disabled:opacity-30"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Message feed */}
      <Card className="botux-table-card flex-1 overflow-hidden flex flex-col min-h-0">
        <div ref={feedRef} className="flex-1 overflow-y-auto p-4 space-y-4">
          {!loadedHistory && (
            <div className="text-center text-muted-foreground text-sm py-8">Loading chat history...</div>
          )}
          {loadedHistory && messages.length === 0 && (
            <div className="text-center text-muted-foreground py-12">
              <Bot className="h-12 w-12 mx-auto mb-3 text-muted-foreground/40" />
              <p className="text-sm">Ask anything about the BOTUX system.</p>
              <p className="text-xs mt-1">Try one of the quick prompts above.</p>
            </div>
          )}
          {messages.map((m, i) => {
            const isUser = m.role === 'user'
            return (
              <div key={i} className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
                {!isUser && (
                  <div className="w-8 h-8 rounded-full border border-border bg-muted flex items-center justify-center flex-shrink-0">
                    <Bot className="h-4 w-4 text-foreground" />
                  </div>
                )}
                  <div className={`max-w-[75%] rounded-2xl px-4 py-3 ${isUser ? 'bg-secondary border border-border' : 'bg-background border border-border'}`}>
                  <p className="text-sm whitespace-pre-wrap leading-relaxed">{m.content}</p>
                  {m.meta && (
                    <div className="mt-2 pt-2 border-t border-border/30 flex items-center gap-3 text-[9px] text-muted-foreground">
                      <span className="flex items-center gap-1"><Zap className="h-2.5 w-2.5" /> {m.meta.model}</span>
                      <span className="flex items-center gap-1"><Clock className="h-2.5 w-2.5" /> {m.meta.ms}ms</span>
                      <span>~{m.meta.tokens} tokens</span>
                    </div>
                  )}
                </div>
                {isUser && (
                  <div className="w-8 h-8 rounded-full border border-border bg-background flex items-center justify-center flex-shrink-0">
                    <UserIcon className="h-4 w-4 text-muted-foreground" />
                  </div>
                )}
              </div>
            )
          })}
          {sending && (
            <div className="flex gap-3 justify-start">
              <div className="w-8 h-8 rounded-full border border-border bg-muted flex items-center justify-center flex-shrink-0">
                <Bot className="h-4 w-4 text-foreground animate-pulse" />
              </div>
              <div className="max-w-[75%] rounded-2xl px-4 py-3 bg-background border border-border">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-foreground animate-pulse" />
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-foreground animate-pulse" style={{animationDelay: '0.2s'}} />
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-foreground animate-pulse" style={{animationDelay: '0.4s'}} />
                  <span className="ml-2">gpt-5.4 is thinking...</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Error banner */}
        {error && (
          <div className="border-t border-border px-4 py-2 flex items-center gap-2 bg-background">
            <AlertCircle className="h-4 w-4 text-red-400" />
            <span className="text-xs text-red-400">{error}</span>
          </div>
        )}

        {/* Input */}
        <div className="border-t border-border/50 p-3 flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Ask about positions, signals, council, learning..."
            disabled={sending}
            className="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-foreground/20 disabled:opacity-50"
          />
          <button
            onClick={() => send()}
            disabled={sending || !input.trim()}
            className="px-4 py-2 bg-[#0ABAB5] text-black rounded-lg text-sm font-medium hover:bg-[#0ABAB5]/80 disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <Send className="h-4 w-4" />
            Send
          </button>
        </div>
      </Card>
    </div>
  )
}
