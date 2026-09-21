import { describe, expect, it } from 'vitest'

import { checkImageFile, detectBarcodeInImage, readImage } from './imageFile'

describe('photo file checks (before sending)', () => {
  it('accepts JPEG, PNG and WebP within the size limit', () => {
    for (const type of ['image/jpeg', 'image/png', 'image/webp']) {
      expect(checkImageFile({ type, size: 1000, name: 'a' })).toBe('ok')
    }
  })

  it('rejects other file types, including a disguised program', () => {
    expect(checkImageFile({ type: 'application/x-msdownload', size: 1000, name: 'photo.jpg.exe' })).toBe('type')
    expect(checkImageFile({ type: 'image/svg+xml', size: 1000, name: 'a.svg' })).toBe('type')
    expect(checkImageFile({ type: 'application/pdf', size: 1000, name: 'a.pdf' })).toBe('type')
  })

  it('rejects a file that is too big or empty', () => {
    expect(checkImageFile({ type: 'image/jpeg', size: 6 * 1024 * 1024, name: 'a.jpg' }, 5 * 1024 * 1024)).toBe('size')
    expect(checkImageFile({ type: 'image/jpeg', size: 0, name: 'a.jpg' })).toBe('empty')
  })

  it('judges an unlabelled file by its name, only for an early message (the server decides for real)', () => {
    expect(checkImageFile({ type: '', size: 10, name: 'shelf.JPG' })).toBe('ok')
    expect(checkImageFile({ type: '', size: 10, name: 'run.sh' })).toBe('type')
  })

  it('encodes a file as base64 without the data: prefix', async () => {
    const file = new File([new Uint8Array([1, 2, 3])], 'a.png', { type: 'image/png' })
    const encoded = await readImage(file)
    expect(encoded.base64).toBe('AQID')
    expect(encoded.contentType).toBe('image/png')
  })

  it('says "unsupported" when the browser cannot read barcodes from a photo', async () => {
    expect(await detectBarcodeInImage(new Blob([]))).toBe('unsupported')
  })
})
