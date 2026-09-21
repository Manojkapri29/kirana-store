import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type ChangeEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { keepProductImage, getProductImage, removeProductImage } from '@/api/imageIntelligence'
import { ApiError } from '@/api/client'
import type { Product } from '@/api/types'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Button } from '@/components/ui'
import { useFailure } from '@/hooks/useFailure'
import { ACCEPT_ATTRIBUTE, blobToDataUrl, checkImageFile, readImage } from '@/lib/imageFile'

/**
 * The photo kept with a product (only when the owner chose to keep one). It is private: it is fetched through the
 * API for this shop, never from a public address. Adding or removing a photo never touches stock or prices.
 */
export function ProductPhotoSection({ product }: { product: Product }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const failure = useFailure()

  const image = useQuery({
    queryKey: ['productImage', product.id],
    // A data address (not a temporary object address) so there is nothing to release afterwards.
    queryFn: async () => blobToDataUrl(await getProductImage(product.id)),
    retry: false, // "no photo" answers 404: that is a normal state, not something to retry
  })
  const url = image.data ?? null

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['productImage', product.id] })
  const attach = useMutation({
    mutationFn: async (file: File) => {
      const encoded = await readImage(file)
      return keepProductImage(product.id, { image_base64: encoded.base64, content_type: encoded.contentType || null })
    },
    onSuccess: refresh,
    onError: (error) => {
      if (error instanceof ApiError && error.status === 422) setProblem(error.message)
      else failure.setFailure(error)
    },
  })
  const remove = useMutation({
    mutationFn: () => removeProductImage(product.id),
    onSuccess: refresh,
    onError: failure.setFailure,
  })

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setProblem(null)
    failure.clear()
    if (checkImageFile(file) !== 'ok') {
      setProblem(t('photo.invalidType'))
      return
    }
    attach.mutate(file)
  }

  const noPhoto = image.error instanceof ApiError && image.error.status === 404
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('photo.photoHeading')}</h2>
      {image.isPending && <p className="text-slate-500">{t('common.loading')}</p>}
      {url && <img src={url} alt={product.name} className="max-h-64 rounded-lg border border-slate-200 object-contain" />}
      {(noPhoto || (!image.isPending && !image.data && !image.isError)) && <p className="text-slate-600">{t('photo.noPhoto')}</p>}
      {image.isError && !noPhoto && <ErrorNotice error={image.error} context="search" retry={() => void image.refetch()} />}
      {url && <p className="text-sm text-slate-500">{t('photo.keptPrivate')}</p>}
      {problem && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {problem}
        </p>
      )}
      {failure.failure !== null && <ErrorNotice error={failure.failure} context="image_upload" retry={() => input.current?.click()} choose_another={() => input.current?.click()} />}
      <input ref={input} type="file" accept={ACCEPT_ATTRIBUTE} className="sr-only" tabIndex={-1} onChange={onFile} />
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" loading={attach.isPending} onClick={() => input.current?.click()}>
          {url ? t('photo.replacePhoto') : t('photo.addPhoto')}
        </Button>
        {url && (
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() => {
              if (window.confirm(t('photo.removeConfirm'))) remove.mutate()
            }}
          >
            {t('photo.removePhoto')}
          </Button>
        )}
      </div>
    </section>
  )
}
