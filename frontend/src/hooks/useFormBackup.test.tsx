import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useFormBackup } from './useFormBackup'

const KEY = 'shop.draft.test.form'

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers()
})
afterEach(() => vi.useRealTimers())

describe('useFormBackup', () => {
  it('saves what is typed, after a short pause', () => {
    const { rerender } = renderHook(({ value }) => useFormBackup('test.form', value), { initialProps: { value: { name: '' } } })
    rerender({ value: { name: 'Sugar' } })
    expect(localStorage.getItem(KEY)).toBeNull() // not on every key press
    act(() => vi.advanceTimersByTime(500))
    expect(JSON.parse(localStorage.getItem(KEY)!).value).toEqual({ name: 'Sugar' })
  })

  it('never saves a form nobody touched (it would replace a good backup with an empty one)', () => {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), value: { name: 'Earlier' } }))
    renderHook(() => useFormBackup('test.form', { name: '' }))
    act(() => vi.advanceTimersByTime(1000))
    expect(JSON.parse(localStorage.getItem(KEY)!).value).toEqual({ name: 'Earlier' })
  })

  it('offers an earlier backup but does not apply it by itself', () => {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), value: { name: 'Earlier' } }))
    const { result } = renderHook(() => useFormBackup('test.form', { name: '' }))
    expect(result.current.restorable).toEqual({ name: 'Earlier' })
  })

  it('drops a backup older than a day', () => {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now() - 25 * 60 * 60 * 1000, value: { name: 'Old' } }))
    const { result } = renderHook(() => useFormBackup('test.form', { name: '' }))
    expect(result.current.restorable).toBeNull()
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('clears the backup once the data is really saved', () => {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), value: { name: 'Earlier' } }))
    const { result } = renderHook(() => useFormBackup('test.form', { name: '' }))
    act(() => result.current.clear())
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(result.current.restorable).toBeNull()
  })

  it('does nothing when it is disabled (for example when editing a saved record)', () => {
    const { rerender } = renderHook(({ value }) => useFormBackup('test.form', value, { enabled: false }), { initialProps: { value: { a: 1 } } })
    rerender({ value: { a: 2 } })
    act(() => vi.advanceTimersByTime(1000))
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('still works when the browser refuses storage', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    const { result, rerender } = renderHook(({ value }) => useFormBackup('test.form', value), { initialProps: { value: { a: 1 } } })
    rerender({ value: { a: 2 } })
    expect(() => act(() => vi.advanceTimersByTime(1000))).not.toThrow()
    expect(result.current.restorable).toBeNull()
    vi.restoreAllMocks()
  })
})
