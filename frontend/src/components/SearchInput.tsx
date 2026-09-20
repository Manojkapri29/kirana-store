import { Search, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  /** Called on Enter. A USB/Bluetooth barcode scanner "types" the code and presses Enter. */
  onEnter?: () => void
  placeholder: string
  autoFocus?: boolean
}

export function SearchInput({ value, onChange, onEnter, placeholder, autoFocus }: SearchInputProps) {
  const { t } = useTranslation()
  return (
    <div className="relative flex-1">
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-slate-400" />
      <input
        type="search"
        value={value}
        autoFocus={autoFocus}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onEnter?.()
        }}
        className="block min-h-12 w-full rounded-lg border border-slate-300 bg-white pl-10 pr-12 text-base placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-emerald-600 [&::-webkit-search-cancel-button]:hidden"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={t('common.clear')}
          className="absolute right-1 top-1/2 flex size-10 -translate-y-1/2 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"
        >
          <X aria-hidden="true" className="size-5" />
        </button>
      )}
    </div>
  )
}
