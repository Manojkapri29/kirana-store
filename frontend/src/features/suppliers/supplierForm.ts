/**
 * Supplier form logic: values, quick validation, and conversion to the API payload.
 *
 * Pure functions with no React in them. The rules mirror the server's lenient ones so the user gets instant
 * feedback; the server checks again, stores each value in one consistent form, and its messages are shown
 * when they differ. Nothing here is specific to any kind of business.
 */

import type { Supplier, SupplierPayload } from '@/api/types'

export interface FormValues {
  name: string
  phone: string
  alternatePhone: string
  email: string
  address: string
  gstin: string
  notes: string
}

export type FieldName = keyof FormValues
export type ErrorCode = 'required' | 'invalidPhone' | 'invalidEmail' | 'invalidGstin'
export type FieldErrors = Partial<Record<FieldName, ErrorCode>>

export const emptyValues = (): FormValues => ({
  name: '',
  phone: '',
  alternatePhone: '',
  email: '',
  address: '',
  gstin: '',
  notes: '',
})

export const valuesFromSupplier = (supplier: Supplier): FormValues => ({
  name: supplier.name,
  phone: supplier.phone ?? '',
  alternatePhone: supplier.alternate_phone ?? '',
  email: supplier.email ?? '',
  address: supplier.address ?? '',
  gstin: supplier.gstin ?? '',
  notes: supplier.notes ?? '',
})

const SEPARATORS = /[\s\-().]/g
// \p{Nd} = any decimal digit, so Devanagari numbers pass here; the server stores them as 0-9.
const PHONE = /^\+?\p{Nd}{6,15}$/u
const EMAIL = /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/

const isPhone = (text: string): boolean => PHONE.test(text.replace(SEPARATORS, ''))

export function validate(values: FormValues): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.name.trim()) errors.name = 'required'
  if (values.phone.trim() && !isPhone(values.phone)) errors.phone = 'invalidPhone'
  if (values.alternatePhone.trim() && !isPhone(values.alternatePhone)) errors.alternatePhone = 'invalidPhone'
  if (values.email.trim() && !EMAIL.test(values.email.trim())) errors.email = 'invalidEmail'
  if (values.gstin.trim() && !GSTIN.test(values.gstin.replace(/\s/g, '').toUpperCase())) {
    errors.gstin = 'invalidGstin'
  }
  return errors
}

const orNull = (value: string): string | null => (value.trim() === '' ? null : value.trim())

/** All editable details. The server compares with what is stored, so unchanged values change nothing. */
export function buildPayload(values: FormValues): SupplierPayload {
  return {
    name: values.name.trim(),
    phone: orNull(values.phone),
    alternate_phone: orNull(values.alternatePhone),
    email: orNull(values.email),
    address: orNull(values.address),
    gstin: orNull(values.gstin),
    notes: orNull(values.notes),
  }
}

/** Maps API field names to form field names, for showing server errors next to the right input. */
export const API_FIELD_TO_FORM_FIELD: Record<string, FieldName> = {
  name: 'name',
  phone: 'phone',
  alternate_phone: 'alternatePhone',
  email: 'email',
  address: 'address',
  gstin: 'gstin',
  notes: 'notes',
}
