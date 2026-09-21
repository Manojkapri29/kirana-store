/**
 * Checks on a photo BEFORE it is sent. These only save a round trip and give a clear message: the server checks
 * the real content of every file again (a file's name or declared type is never trusted).
 */

export const ACCEPTED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const
export const ACCEPT_ATTRIBUTE = ACCEPTED_IMAGE_TYPES.join(',')
export const DEFAULT_MAX_BYTES = 5 * 1024 * 1024

export type FileCheck = 'ok' | 'type' | 'size' | 'empty'

export function checkImageFile(file: { type: string; size: number; name: string }, maxBytes = DEFAULT_MAX_BYTES): FileCheck {
  if (file.size <= 0) return 'empty'
  const declared = file.type.toLowerCase()
  const byName = /\.(jpe?g|png|webp)$/i.test(file.name)
  // An unlabelled file is judged by its name only for this early message; the server decides for real.
  const typeOk = declared === '' ? byName : (ACCEPTED_IMAGE_TYPES as readonly string[]).includes(declared)
  if (!typeOk) return 'type'
  if (file.size > maxBytes) return 'size'
  return 'ok'
}

export interface EncodedImage {
  base64: string
  contentType: string
}

/** The file as base64 (no "data:" prefix), the way the API takes it. */
export function readImage(file: File): Promise<EncodedImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('unreadable'))
    reader.onload = () => {
      const text = typeof reader.result === 'string' ? reader.result : ''
      const comma = text.indexOf(',')
      if (comma === -1) {
        reject(new Error('unreadable'))
        return
      }
      resolve({ base64: text.slice(comma + 1), contentType: file.type })
    }
    reader.readAsDataURL(file)
  })
}

interface BarcodeDetectorLike {
  detect(source: ImageBitmap): Promise<{ rawValue: string }[]>
}
type BarcodeDetectorConstructor = new (options?: { formats: string[] }) => BarcodeDetectorLike

/**
 * Reads a product barcode from the photo when the browser can (`BarcodeDetector`). `'unsupported'` means this
 * browser cannot; `null` means it looked and found none. Either way the person can type the barcode.
 * A number read here is only a hint: the server checks it (the checksum) before using it.
 */
export async function detectBarcodeInImage(file: Blob): Promise<string | null | 'unsupported'> {
  const Detector = (globalThis as { BarcodeDetector?: BarcodeDetectorConstructor }).BarcodeDetector
  if (!Detector || typeof createImageBitmap !== 'function') return 'unsupported'
  try {
    const detector = new Detector({ formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128'] })
    const bitmap = await createImageBitmap(file)
    try {
      const found = await detector.detect(bitmap)
      return found[0]?.rawValue ?? null
    } finally {
      bitmap.close()
    }
  } catch {
    return null
  }
}

/** A blob as a `data:` address for an <img>. */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('unreadable'))
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.readAsDataURL(blob)
  })
}
