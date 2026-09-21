import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'

const SECRET = 'TypeError: secret detail at /Users/dev/app/src/Thing.tsx:42'

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error(SECRET)
  return <p>All good</p>
}

function Harness() {
  const [explode, setExplode] = useState(true)
  return (
    <>
      <button type="button" onClick={() => setExplode(false)}>
        fix it
      </button>
      <ErrorBoundary>
        <Bomb explode={explode} />
      </ErrorBoundary>
    </>
  )
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined) // React logs the caught error; keep the output clean
})
afterEach(() => vi.restoreAllMocks())

describe('ErrorBoundary', () => {
  it('shows a calm message instead of a blank page, with a way out', () => {
    render(<Harness />)
    expect(screen.getByText("This section couldn’t be loaded.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Dashboard' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
  })

  it('never shows the error, its message, a path or a stack', () => {
    const { container } = render(<Harness />)
    const text = container.textContent ?? ''
    expect(text).not.toContain('secret detail')
    expect(text).not.toContain('/Users/')
    expect(text).not.toContain('TypeError')
  })

  it('renders the content again once Try again is pressed and the cause is gone', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'fix it' }))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByText('All good')).toBeInTheDocument()
  })

  it('reloads the page on request', () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    expect(reload).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
