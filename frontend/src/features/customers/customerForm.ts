/**
 * Customer form logic: values, quick validation, and conversion to the API payload.
 *
 * Pure functions with no React in them. The rules mirror the server's lenient ones for instant feedback; the
 * server checks again and stores each value in one consistent form. Nothing here is specific to any business.
 */

import type { Customer, CustomerPayload } from '@/api/types'
import { checkDecimal, isZero } from '@/lib/decimal'

export interface FormValues {
  name: string
  phone: string
  email: string
  address: string
  notes: string
  openingBalance: string
}

export type FieldName = keyof FormValues
export type AmountErrorCode = 'invalidAmount' | 'amountDecimals' | 'amountZero'
export type ErrorCode = 'required' | 'invalidPhone' | 'invalidEmail' | AmountErrorCode
export type FieldErrors = Partial<Record<FieldName, ErrorCode>>

export const emptyValues = (): FormValues => ({
  name: '',
  phone: '',
  email: '',
  address: '',
  notes: '',
  openingBalance: '',
})

export const valuesFromCustomer = (customer: Customer): FormValues => ({
  name: customer.name,
  phone: customer.phone ?? '',
  email: customer.email ?? '',
  address: customer.address ?? '',
  notes: customer.notes ?? '',
  openingBalance: '',
})

const SEPARATORS = /[\s\-().]/g
const PHONE = /^\+?\p{Nd}{6,15}$/u // any decimal digit: the server stores Devanagari numbers as 0-9
const EMAIL = /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/

/** An amount field: empty is allowed only when `optional`. Returns the error code, or `undefined` if fine. */
export function amountError(text: string, optional = false): AmountErrorCode | undefined {
  const check = checkDecimal(text, 2)
  if (check === 'empty') return optional ? undefined : 'invalidAmount'
  if (check === 'invalid') return 'invalidAmount'
  if (check === 'tooManyDecimals') return 'amountDecimals'
  return isZero(text) ? 'amountZero' : undefined
}

/** `creating` also checks the opening balance, which is only entered when a customer is added. */
export function validate(values: FormValues, creating: boolean): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.name.trim()) errors.name = 'required'
  if (values.phone.trim() && !PHONE.test(values.phone.replace(SEPARATORS, ''))) errors.phone = 'invalidPhone'
  if (values.email.trim() && !EMAIL.test(values.email.trim())) errors.email = 'invalidEmail'
  if (creating) {
    const problem = amountError(values.openingBalance, true)
    if (problem) errors.openingBalance = problem
  }
  return errors
}

const orNull = (value: string): string | null => (value.trim() === '' ? null : value.trim())

export function buildPayload(values: FormValues): CustomerPayload {
  return {
    name: values.name.trim(),
    phone: orNull(values.phone),
    email: orNull(values.email),
    address: orNull(values.address),
    notes: orNull(values.notes),
  }
}

/** Maps API field names to form field names, for showing server errors next to the right input. */
export const API_FIELD_TO_FORM_FIELD: Record<string, FieldName> = {
  name: 'name',
  phone: 'phone',
  email: 'email',
  address: 'address',
  notes: 'notes',
  opening_balance: 'openingBalance',
  amount: 'openingBalance',
}
