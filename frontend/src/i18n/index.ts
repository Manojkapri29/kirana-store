import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import { en } from './locales/en'

export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी' },
] as const

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code']

const STORAGE_KEY = 'kirana.language'

// Typed translation keys: `t('nav.typo')` fails at compile time.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    resources: { translation: typeof en }
  }
}

function isSupported(code: string | null): code is LanguageCode {
  return SUPPORTED_LANGUAGES.some((language) => language.code === code)
}

function readSavedLanguage(): LanguageCode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isSupported(saved)) return saved
  } catch {
    // Storage can be unavailable (private mode, blocked cookies): fall back to English.
  }
  return 'en'
}

/** Hindi is a separate download: an English-language phone never fetches it. */
async function loadLanguage(code: LanguageCode): Promise<void> {
  if (code === 'hi' && !i18n.hasResourceBundle('hi', 'translation')) {
    const { hi } = await import('./locales/hi')
    i18n.addResourceBundle('hi', 'translation', hi, true, true)
  }
}

export function changeLanguage(code: LanguageCode): void {
  void loadLanguage(code).then(() => i18n.changeLanguage(code))
  try {
    localStorage.setItem(STORAGE_KEY, code)
  } catch {
    // Not persisting the choice is acceptable.
  }
}

const initialLanguage = readSavedLanguage()

/** Resolves when the saved language is ready, so the first screen never flashes English keys. */
export const i18nReady: Promise<void> = i18n
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en } },
    lng: 'en', // start in English; switch once the saved language is loaded
    fallbackLng: 'en',
    interpolation: { escapeValue: false }, // React already escapes output
  })
  .then(() => loadLanguage(initialLanguage))
  .then(() => i18n.changeLanguage(initialLanguage))
  .then(() => undefined)

// Keep <html lang> in sync for screen readers and correct Devanagari font selection.
document.documentElement.lang = initialLanguage
i18n.on('languageChanged', (code) => {
  document.documentElement.lang = code
})

export default i18n
