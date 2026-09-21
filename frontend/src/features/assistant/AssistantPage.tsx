import { useMutation, useQuery } from '@tanstack/react-query'
import { Send, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { askAssistant, getAiStatus, runAiTool, type Answer } from '@/api/ai'
import { ApiError } from '@/api/client'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Button, PageHeader, Spinner } from '@/components/ui'

import { ActionPreviewCard } from './ActionPreviewCard'
import { AnswerView } from './AnswerView'
import { DocumentsPanel } from './DocumentsPanel'
import { useProposal } from './useProposal'

type Tab = 'ask' | 'insights' | 'documents'

interface Message {
  id: number
  question: string
  answer: Answer | null
  error: unknown
  pending: boolean
}

const STORAGE_KEY = 'assistant.chat'
const REPORTS = [
  'get_insights',
  'get_anomalies',
  'get_reorder_recommendations',
  'get_purchase_suggestions',
  'get_slow_moving_products',
  'get_declining_products',
  'get_category_performance',
  'get_promotion_ideas',
  'get_business_report',
] as const

function loadChat(): Message[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Message[]).map((m) => ({ ...m, pending: false })) : []
  } catch {
    return [] // storage can be unavailable: the conversation is a convenience, never required
  }
}

/**
 * The Business Assistant. It answers from the shop's own records (read-only), can prepare drafts that need an
 * explicit confirmation, and reads photographed documents into drafts. The conversation lives only in this browser
 * tab (session storage): the server keeps no question text and no conversation memory.
 */
export function AssistantPage() {
  const { t, i18n } = useTranslation()
  const [tab, setTab] = useState<Tab>('ask')
  const status = useQuery({ queryKey: ['aiStatus', i18n.language], queryFn: () => getAiStatus(i18n.language), staleTime: 30_000 })
  const features = status.data?.features

  if (status.isPending) return <Spinner />
  const tabClass = (name: Tab) =>
    `min-h-12 rounded-lg px-5 font-medium ${tab === name ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50'}`

  return (
    <div className="space-y-6">
      <PageHeader title={t('assistant.title')} subtitle={t('assistant.subtitle')} />
      <p className="text-sm text-slate-600">{t('assistant.readOnly')}</p>
      {status.isError && <ErrorNotice error={status.error} context="ai" retry={() => void status.refetch()} />}
      {features?.ai_assistant === false && <Alert tone="warning">{t('assistant.planNeeded')}</Alert>}
      {status.data && !status.data.configured && (
        <Alert tone="info">
          <p className="font-semibold">{t('assistant.notConfigured')}</p>
          <p className="text-sm">{t('assistant.notConfiguredHint')}</p>
        </Alert>
      )}
      {status.data?.usage && (
        <p className="text-sm text-slate-500">
          {status.data.usage.limit === null
            ? t('assistant.usageUnlimited', { used: status.data.usage.requests })
            : t('assistant.usage', { used: status.data.usage.requests, limit: status.data.usage.limit })}
        </p>
      )}
      <div className="flex flex-wrap gap-3" role="tablist">
        {(['ask', 'insights', 'documents'] as const).map((name) => (
          <button key={name} type="button" role="tab" aria-selected={tab === name} className={tabClass(name)} onClick={() => setTab(name)}>
            {t(`assistant.tabs.${name}`)}
          </button>
        ))}
      </div>
      {features?.ai_assistant !== false && tab === 'ask' && <AskTab suggestions={status.data?.suggestions ?? []} />}
      {features?.ai_assistant !== false && tab === 'insights' && <InsightsTab allowed={features?.ai_insights !== false} />}
      {tab === 'documents' && <DocumentsPanel allowed={features?.ai_documents !== false} configured={status.data?.documents ?? false} />}
    </div>
  )
}

function AskTab({ suggestions }: { suggestions: { label: string; question: string }[] }) {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  const [text, setText] = useState('')
  const [messages, setMessages] = useState<Message[]>(loadChat)
  const { action, setAction, propose, close } = useProposal()
  const [actionFor, setActionFor] = useState<number | null>(null)
  const nextId = useRef(messages.reduce((max, m) => Math.max(max, m.id), 0) + 1)
  const asked = useRef(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.filter((m) => !m.pending).slice(-20).map((m) => ({ ...m, error: null }))))
    } catch {
      // not saving the conversation is fine
    }
    bottom.current?.scrollIntoView?.({ block: 'nearest' })
  }, [messages])

  const ask = useMutation({
    mutationFn: ({ question }: { id: number; question: string }) => askAssistant(question, i18n.language),
    onSuccess: (answer, { id }) => setMessages((all) => all.map((m) => (m.id === id ? { ...m, answer, pending: false, error: null } : m))),
    onError: (error, { id }) => setMessages((all) => all.map((m) => (m.id === id ? { ...m, pending: false, error } : m))),
  })

  function send(question: string) {
    const trimmed = question.trim()
    if (!trimmed || ask.isPending) return
    const id = nextId.current++
    setMessages((all) => [...all, { id, question: trimmed, answer: null, error: null, pending: true }])
    setText('')
    ask.mutate({ id, question: trimmed })
  }

  // The dashboard's "Ask your Business Assistant" box passes its question in the address (?q=...).
  useEffect(() => {
    const q = params.get('q')
    if (q && !asked.current) {
      asked.current = true
      params.delete('q')
      setParams(params, { replace: true })
      send(q)
    }
  })

  function retry(message: Message) {
    setMessages((all) => all.map((m) => (m.id === message.id ? { ...m, pending: true, error: null } : m)))
    ask.mutate({ id: message.id, question: message.question })
  }

  return (
    <div className="space-y-4">
      <form
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          send(text)
        }}
        className="flex gap-3"
      >
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={t('assistant.placeholder')}
          aria-label={t('assistant.askHeading')}
          maxLength={600}
          className="block min-h-12 flex-1 rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
        />
        <Button type="submit" disabled={ask.isPending || text.trim() === ''}>
          <Send aria-hidden="true" className="size-5" />
          {t('assistant.ask')}
        </Button>
      </form>

      {suggestions.length > 0 && (
        <div>
          <p className="mb-1 text-sm text-slate-600">{t('assistant.tryAsking')}</p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s.label}
                type="button"
                disabled={ask.isPending}
                onClick={() => send(s.question)}
                className="min-h-10 rounded-full border border-slate-300 bg-white px-3 text-sm text-slate-700 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-emerald-600 disabled:opacity-60"
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <ul className="space-y-4" aria-live="polite">
        {messages.map((message) => (
          <li key={message.id} className="space-y-2">
            <div className="ml-auto max-w-xl rounded-xl bg-emerald-600 px-4 py-2 text-white">
              <span className="sr-only">{t('assistant.you')}: </span>
              {message.question}
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <Sparkles aria-hidden="true" className="size-4" />
                {t('assistant.assistant')}
              </p>
              {message.pending && <p className="text-slate-600" role="status">{t('assistant.asking')}</p>}
              {message.error !== null && (
                <ErrorNotice error={message.error} context="ai" safeToRepeat retry={() => retry(message)} />
              )}
              {message.answer && (
                <AnswerView
                  answer={message.answer}
                  onFollowUp={send}
                  proposing={propose.isPending}
                  onPropose={(proposal) => {
                    setActionFor(message.id)
                    propose.mutate(proposal)
                  }}
                />
              )}
              {actionFor === message.id && propose.isError && <ErrorNotice error={propose.error} context="save" safeToRepeat retry={() => propose.reset()} />}
              {actionFor === message.id && action && <div className="mt-3"><ActionPreviewCard action={action} onChange={setAction} onClose={close} /></div>}
            </div>
          </li>
        ))}
        <div ref={bottom} />
      </ul>

      {messages.length > 0 && (
        <Button
          variant="secondary"
          onClick={() => {
            setMessages([])
            close()
          }}
        >
          {t('assistant.clear')}
        </Button>
      )}
    </div>
  )
}

function InsightsTab({ allowed }: { allowed: boolean }) {
  const { t, i18n } = useTranslation()
  const [tool, setTool] = useState<string | null>(null)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const { action, setAction, propose, close } = useProposal()
  const run = useMutation({
    mutationFn: (name: string) => runAiTool(name, i18n.language),
    onSuccess: (result) => {
      setAnswer(result)
      close()
    },
  })
  const planError = run.error instanceof ApiError && run.error.category === 'plan_limit'

  if (!allowed) return <Alert tone="warning">{t('assistant.planNeeded')}</Alert>
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">{t('assistant.insights.heading')}</h2>
        <p className="text-sm text-slate-600">{t('assistant.insights.hint')}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {REPORTS.map((name) => (
          <Button
            key={name}
            variant={tool === name ? 'primary' : 'secondary'}
            disabled={run.isPending}
            onClick={() => {
              setTool(name)
              run.mutate(name)
            }}
          >
            {t(`assistant.insights.reports.${name}`)}
          </Button>
        ))}
      </div>
      {run.isPending && <p className="text-slate-600" role="status">{t('assistant.insights.loading')}</p>}
      {run.isError && (planError ? <Alert tone="warning">{(run.error as ApiError).message}</Alert> : <ErrorNotice error={run.error} context="ai" safeToRepeat retry={() => tool && run.mutate(tool)} />)}
      {answer && !run.isPending && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <AnswerView answer={answer} proposing={propose.isPending} onPropose={(proposal) => propose.mutate(proposal)} />
          {propose.isError && <ErrorNotice error={propose.error} context="save" safeToRepeat retry={() => propose.reset()} />}
          {action && <div className="mt-3"><ActionPreviewCard action={action} onChange={setAction} onClose={close} /></div>}
        </div>
      )}
    </div>
  )
}
