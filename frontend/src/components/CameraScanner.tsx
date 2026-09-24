import { Camera, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, Button } from '@/components/ui'

import { cameraScanSupported, detectorCtor, FORMATS } from './cameraSupport'

/**
 * A camera button that returns the code it reads to the SAME handler a typed or keyboard-scanned code goes to, so no lookup logic is
 * duplicated here. The camera runs only while the panel is open, nothing is recorded or uploaded, and the stream is stopped on close.
 */
export function CameraScanButton({ onCode }: { onCode: (code: string) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const video = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (!open) return
    let stopped = false
    let stream: MediaStream | null = null
    let timer: number | undefined
    const Detector = detectorCtor()
    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
        if (stopped || !video.current || !Detector) return
        video.current.srcObject = stream
        await video.current.play()
        const detector = new Detector({ formats: FORMATS })
        const look = async () => {
          if (stopped || !video.current) return
          try {
            const found = await detector.detect(video.current)
            if (found[0]?.rawValue) {
              onCode(found[0].rawValue)
              setOpen(false)
              return
            }
          } catch {
            // A frame that cannot be read is just skipped.
          }
          timer = window.setTimeout(look, 300)
        }
        void look()
      } catch {
        setProblem(t('scanner.cameraDenied'))
      }
    }
    void start()
    return () => {
      stopped = true
      window.clearTimeout(timer)
      stream?.getTracks().forEach((track) => track.stop())
    }
  }, [open, onCode, t])

  if (!cameraScanSupported()) return null
  return (
    <>
      <Button variant="secondary" aria-label={t('scanner.useCamera')} onClick={() => { setProblem(null); setOpen(true) }}>
        <Camera aria-hidden="true" className="size-5" />
      </Button>
      {open && (
        <div role="dialog" aria-modal="true" aria-label={t('scanner.cameraTitle')} className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/80 p-4">
          <video ref={video} playsInline muted className="max-h-[60dvh] w-full max-w-md rounded-xl bg-black" />
          <p className="text-sm text-white">{t('scanner.cameraHint')}</p>
          {problem && <Alert tone="warning">{problem}</Alert>}
          <Button variant="secondary" onClick={() => setOpen(false)}><X aria-hidden="true" className="size-5" />{t('scanner.closeCamera')}</Button>
        </div>
      )}
    </>
  )
}
