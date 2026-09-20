import { useTranslation } from 'react-i18next'

/**
 * Returns a function that turns a business type code into a label in the current language.
 * A type added later (a new database row) has no translation yet, so its English name from the server is used.
 */
export function useBusinessTypeLabel(): (code: string, fallbackName: string) => string {
  const { t, i18n } = useTranslation()
  return (code, fallbackName) =>
    i18n.exists(`businessTypes.${code}`) ? t(`businessTypes.${code}` as 'businessTypes.OTHER') : fallbackName
}
