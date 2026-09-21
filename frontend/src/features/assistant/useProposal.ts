import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { proposeAction, type AiAction, type Proposal } from '@/api/ai'

/** Turns an assistant proposal into a reviewable action (nothing is created by this: it only prepares the preview). */
export function useProposal() {
  const [action, setAction] = useState<AiAction | null>(null)
  const propose = useMutation({
    mutationFn: (proposal: Proposal) => proposeAction(proposal.kind, proposal.feature, proposal.payload),
    onSuccess: setAction,
  })
  return { action, setAction, propose, close: () => setAction(null) }
}
