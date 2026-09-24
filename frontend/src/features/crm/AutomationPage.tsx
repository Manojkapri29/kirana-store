import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { createRule, listRules, runRule, setRuleActive, type AutomationRule, type RuleAction, type Trigger } from '@/api/crm'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'

import { CrmTabs } from './common'
import { useErrorText } from './crmUtils'

const TRIGGERS: readonly Trigger[] = ['NEW_CUSTOMER', 'INACTIVITY', 'LOYALTY_MILESTONE', 'PURCHASE_MILESTONE']
const ACTIONS: readonly RuleAction[] = ['CREATE_CAMPAIGN_DRAFT', 'CREATE_TASK', 'NOTIFY']
/** The one condition each trigger needs, and its default. */
type ConditionLabel = 'withinDays' | 'inactiveDays' | 'pointsThreshold' | 'purchaseThreshold'
const CONDITION: Record<Trigger, { key: string; label: ConditionLabel; value: number }> = {
  NEW_CUSTOMER: { key: 'within_days', label: 'withinDays', value: 1 },
  INACTIVITY: { key: 'inactive_days', label: 'inactiveDays', value: 60 },
  LOYALTY_MILESTONE: { key: 'points_threshold', label: 'pointsThreshold', value: 500 },
  PURCHASE_MILESTONE: { key: 'purchase_count_threshold', label: 'purchaseThreshold', value: 10 },
}

function NewRuleForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState<Trigger>('INACTIVITY')
  const [threshold, setThreshold] = useState(String(CONDITION.INACTIVITY.value))
  const [action, setAction] = useState<RuleAction>('CREATE_TASK')
  const [cooldown, setCooldown] = useState('30')
  const create = useMutation({
    mutationFn: () =>
      createRule({
        name, trigger_type: trigger, conditions: { [CONDITION[trigger].key]: Number(threshold) },
        action_type: action, action_config: {}, cooldown_days: Number(cooldown) || 0,
      }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['automationRules'] }); onDone() },
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
      <TextField label={t('crm.automation.name')} value={name} onChange={(e) => setName(e.target.value)} required />
      <div className="grid gap-4 md:grid-cols-2">
        <SelectField label={t('crm.automation.trigger')} value={trigger} onChange={(e) => { const next = e.target.value as Trigger; setTrigger(next); setThreshold(String(CONDITION[next].value)) }}>
          {TRIGGERS.map((k) => <option key={k} value={k}>{t(`crm.automation.triggers.${k}`)}</option>)}
        </SelectField>
        <TextField label={t(`crm.automation.conditions.${CONDITION[trigger].label}`)} type="number" min={0} value={threshold} onChange={(e) => setThreshold(e.target.value)} required />
        <SelectField label={t('crm.automation.action')} value={action} onChange={(e) => setAction(e.target.value as RuleAction)}>
          {ACTIONS.map((k) => <option key={k} value={k}>{t(`crm.automation.actions.${k}`)}</option>)}
        </SelectField>
        <TextField label={t('crm.automation.cooldown')} type="number" min={0} value={cooldown} onChange={(e) => setCooldown(e.target.value)} hint={t('crm.automation.cooldownHint')} />
      </div>
      <Alert tone="info">{t('crm.automation.safeNote')}</Alert>
      {create.isError && <Alert tone="error">{errorText(create.error)}</Alert>}
      <div className="flex gap-3">
        <Button type="submit" loading={create.isPending} disabled={!name.trim()}>{t('crm.automation.create')}</Button>
        <Button variant="secondary" onClick={onDone}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}

function RuleCard({ rule }: { rule: AutomationRule }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: () => setRuleActive(rule.id, !rule.is_active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['automationRules'] }),
  })
  const run = useMutation({ mutationFn: () => runRule(rule.id) })
  return (
    <li className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-semibold">{rule.name}</h3>
        <Badge tone={rule.is_active ? 'green' : 'slate'}>{rule.is_active ? t('crm.automation.on') : t('crm.automation.off')}</Badge>
      </div>
      <p className="text-sm text-slate-600">
        {t(`crm.automation.triggers.${rule.trigger_type}`)} → {t(`crm.automation.actions.${rule.action_type}`)} · {t('crm.automation.cooldownDays', { count: rule.cooldown_days })}
      </p>
      {toggle.isError && <Alert tone="error">{errorText(toggle.error)}</Alert>}
      {run.isError && <Alert tone="error">{errorText(run.error)}</Alert>}
      {run.data && (
        <Alert tone={run.data.run_status === 'FAILED' ? 'error' : 'success'}>
          {t('crm.automation.result', { matched: run.data.customers_matched, actioned: run.data.customers_actioned })} {run.data.detail}
        </Alert>
      )}
      <div className="flex gap-3">
        <Button variant="secondary" requires="AUTOMATION_MANAGE" loading={toggle.isPending} onClick={() => toggle.mutate()}>{rule.is_active ? t('crm.automation.turnOff') : t('crm.automation.turnOn')}</Button>
        <Button variant="secondary" requires="AUTOMATION_MANAGE" loading={run.isPending} disabled={!rule.is_active} onClick={() => run.mutate()}>{t('crm.automation.runNow')}</Button>
      </div>
    </li>
  )
}

export function AutomationPage() {
  const { t } = useTranslation()
  const [creating, setCreating] = useState(false)
  const rules = useQuery({ queryKey: ['automationRules'], queryFn: listRules })
  return (
    <div className="space-y-6">
      <PageHeader
        title={t('crm.automation.title')}
        subtitle={t('crm.automation.subtitle')}
        actions={<Button requires="AUTOMATION_MANAGE" onClick={() => setCreating(true)}><Plus aria-hidden="true" className="size-5" />{t('crm.automation.add')}</Button>}
      />
      <CrmTabs />
      <Alert tone="info">{t('crm.automation.manualNote')}</Alert>
      {creating && <NewRuleForm onDone={() => setCreating(false)} />}
      {rules.isError && <QueryError error={rules.error} onRetry={() => void rules.refetch()} />}
      {rules.isPending && <Spinner />}
      {rules.data && rules.data.items.length === 0 && <EmptyState title={t('crm.automation.empty')} />}
      {rules.data && rules.data.items.length > 0 && <ul className="space-y-3">{rules.data.items.map((r) => <RuleCard key={r.id} rule={r} />)}</ul>}
    </div>
  )
}
