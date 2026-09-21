import { useTranslation } from 'react-i18next'

import type { Answer, Proposal } from '@/api/ai'
import type { ExportKind } from '@/api/exports'
import { ExportButtons } from '@/components/ExportButtons'
import { Alert, Badge, Button } from '@/components/ui'

interface Props {
  answer: Answer
  /** Called when the person asks for a draft. Nothing is created by this: it opens a preview to confirm. */
  onPropose?: (proposal: Proposal) => void
  onFollowUp?: (question: string) => void
  proposing?: boolean
}

const STATUS_TONE = { NO_DATA: 'slate', NOT_AVAILABLE: 'amber', NOT_UNDERSTOOD: 'amber', NOT_CONFIGURED: 'amber', REFUSED: 'red' } as const

/**
 * One answer: the message, the real figures, where they came from and what was left out. Every number here was read
 * from the shop's records by the backend; this component only lays it out. Proposals are buttons that PREPARE a
 * draft for review (they change nothing).
 */
export function AnswerView({ answer, onPropose, onFollowUp, proposing = false }: Props) {
  const { t } = useTranslation()
  const tone = answer.status in STATUS_TONE ? STATUS_TONE[answer.status as keyof typeof STATUS_TONE] : null
  const recommendation = answer.badges.includes('AI Recommendation')
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {tone && <Badge tone={tone}>{t(`assistant.statusLabels.${answer.status as keyof typeof STATUS_TONE}`)}</Badge>}
        {recommendation && <Badge tone="green">{t('assistant.badges.AI Recommendation')}</Badge>}
      </div>
      <p className="text-slate-900">{answer.message}</p>
      {recommendation && <p className="text-sm text-slate-600">{t('assistant.recommendationNote')}</p>}

      {answer.figures.length > 0 && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {answer.figures.map((figure) => (
            <div key={figure.label} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <dt className="text-xs text-slate-600">{figure.label}</dt>
              <dd className="text-lg font-bold text-slate-900">{figure.value}</dd>
              {figure.note && <p className="text-xs text-slate-500">{figure.note}</p>}
            </div>
          ))}
        </dl>
      )}

      {answer.table && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                {answer.table.columns.map((column) => (
                  <th key={column} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {answer.table.rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="px-3 py-2 align-top">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {answer.notes.map((note) => (
        <p key={note} className="text-sm text-slate-600">
          {note}
        </p>
      ))}

      {answer.sources.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-500">
          {answer.sources.map((source) => (
            <li key={source}>{source}</li>
          ))}
        </ul>
      )}

      {answer.export && (
        <div>
          <p className="mb-1 text-sm font-medium text-slate-700">{t('assistant.download')}</p>
          <ExportButtons kind={answer.export.kind as ExportKind} filters={answer.export.filters} />
        </div>
      )}

      {answer.proposals.length > 0 && onPropose && (
        <div className="space-y-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
          <p className="text-sm text-emerald-900">{t('assistant.proposals.hint')}</p>
          <div className="flex flex-wrap gap-2">
            {answer.proposals.map((proposal) => (
              <Button key={proposal.label} variant="secondary" disabled={proposing} onClick={() => onPropose(proposal)}>
                {proposal.label}
              </Button>
            ))}
          </div>
        </div>
      )}

      {answer.status === 'NOT_CONFIGURED' && <Alert tone="info">{t('assistant.notConfiguredHint')}</Alert>}

      {answer.follow_ups.length > 0 && onFollowUp && (
        <div>
          <p className="mb-1 text-sm text-slate-600">{t('assistant.followUp')}</p>
          <div className="flex flex-wrap gap-2">
            {answer.follow_ups.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => onFollowUp(question)}
                className="min-h-10 rounded-full border border-slate-300 bg-white px-3 text-sm text-slate-700 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-emerald-600"
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
