export interface DetectedBarcode { rawValue: string }
export interface BarcodeDetectorLike { detect: (source: CanvasImageSource) => Promise<DetectedBarcode[]> }
export type BarcodeDetectorCtor = new (options?: { formats?: string[] }) => BarcodeDetectorLike

export const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'qr_code']

export const detectorCtor = (): BarcodeDetectorCtor | null => {
  const ctor = (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector
  return ctor ?? null
}

/** Can this browser scan with the camera? (Chrome and Edge on Android and desktop, some Safari builds.) Otherwise a USB/Bluetooth scanner or typing still work. */
export const cameraScanSupported = (): boolean => Boolean(detectorCtor()) && typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia)
