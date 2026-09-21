import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

import { ErrorNotice } from './ErrorNotice'

const unexpected = new ApiError('SQLAlchemy OperationalError: database is locked', 500, {}, {}, {
  category: 'unexpected',
  referenceId: 'ERR-20260921-A82F5',
})
const offline = new ApiError('The connection was interrupted.', 0, {}, {}, { category: 'network', retryable: true })

describe('ErrorNotice', () => {
  it('shows a friendly title, the reference and that the data is kept, never the raw error', () => {
    const { container } = render(<ErrorNotice error={unexpected} context="save" cancel={() => undefined} />)
    expect(screen.getByText('We couldn’t save your changes.')).toBeInTheDocument()
    expect(screen.getByText('Reference: ERR-20260921-A82F5')).toBeInTheDocument()
    expect(screen.getByText('Your data has been preserved on this screen.')).toBeInTheDocument()
    expect(container.textContent).not.toContain('SQLAlchemy')
    expect(container.textContent).not.toContain('database is locked')
  })

  it('runs "Try again" when a repeat is safe', () => {
    const retry = vi.fn()
    render(<ErrorNotice error={offline} context="checkout" safeToRepeat retry={retry} go_back={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('does not offer "Try again" for a write that is not safe to repeat', () => {
    render(<ErrorNotice error={offline} context="checkout" safeToRepeat={false} retry={() => undefined} go_back={() => undefined} />)
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('offers only the actions the screen can actually do', () => {
    render(<ErrorNotice error={offline} context="checkout" safeToRepeat retry={() => undefined} />)
    expect(screen.queryByRole('button', { name: 'Save as draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Go back' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('never leaves a person without a next step', () => {
    render(<ErrorNotice error={offline} context="checkout" safeToRepeat={false} />)
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  })

  it('offers "Continue without comparison" for a price service that is down, using the given label', () => {
    const onContinue = vi.fn()
    render(
      <ErrorNotice
        error={new ApiError('x', 503, {}, {}, { category: 'external_api', retryable: true })}
        context="price"
        safeToRepeat
        retry={() => undefined}
        continue={onContinue}
        labels={{ continue: 'Continue without comparison' }}
      />,
    )
    expect(screen.getByText('Market price couldn’t be checked right now.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue without comparison' }))
    expect(onContinue).toHaveBeenCalled()
  })

  it('shows the readable message of a stock conflict and lets the cart be adjusted', () => {
    const adjust = vi.fn()
    render(
      <ErrorNotice
        error={new ApiError('Only 3 kg of Sugar available.', 409, {}, {}, { category: 'insufficient_stock' })}
        context="checkout"
        adjust_cart={adjust}
        go_back={() => undefined}
      />,
    )
    expect(screen.getByText('This product is no longer available in the requested quantity.')).toBeInTheDocument()
    expect(screen.getByText('Only 3 kg of Sugar available.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Adjust the cart' }))
    expect(adjust).toHaveBeenCalled()
  })
})
