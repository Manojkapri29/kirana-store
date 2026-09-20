// Kept apart from the components so files that export components stay fast-refresh friendly.

// Large touch targets (min-h-12 = 48px) everywhere: the users are not always comfortable with small buttons.

export type ButtonVariant = 'primary' | 'secondary' | 'danger'

const BUTTON_BASE =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-lg px-5 text-base font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 disabled:cursor-not-allowed disabled:opacity-60'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-emerald-600 text-white hover:bg-emerald-700',
  secondary: 'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50',
  danger: 'border border-red-300 bg-white text-red-700 hover:bg-red-50',
}

export const buttonClasses = (variant: ButtonVariant = 'primary') => `${BUTTON_BASE} ${BUTTON_VARIANTS[variant]}`
