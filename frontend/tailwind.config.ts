import type { Config } from 'tailwindcss';

/**
 * A restrained, instrument-like palette. This product asks people to trust it
 * with a six-figure decision, so the design language is closer to an analyst's
 * report than a consumer app: a neutral ink scale, one deep teal accent, and
 * verdict colours used sparingly and only where they carry meaning.
 *
 * Every colour resolves through a CSS variable defined in globals.css, and the
 * `ink` ramp *inverts* under `prefers-color-scheme: dark`. That is what makes
 * dark mode a property of the tokens rather than a `dark:` variant sprayed
 * across every component: `text-ink-900` is near-black on paper and near-white
 * at night, with no second class name and no chance of the two drifting apart.
 */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Inverting neutral ramp: 50 is the page, 950 is the strongest text.
        ink: {
          50: token('ink-50'),
          100: token('ink-100'),
          200: token('ink-200'),
          300: token('ink-300'),
          400: token('ink-400'),
          500: token('ink-500'),
          600: token('ink-600'),
          700: token('ink-700'),
          800: token('ink-800'),
          900: token('ink-900'),
          950: token('ink-950'),
        },
        // Elevation, named for what it is rather than for a grey value.
        surface: {
          DEFAULT: token('surface'),
          sunken: token('surface-sunken'),
          raised: token('surface-raised'),
        },
        accent: {
          50: token('accent-50'),
          100: token('accent-100'),
          200: token('accent-200'),
          300: token('accent-300'),
          400: token('accent-400'),
          500: token('accent-500'),
          600: token('accent-600'),
          700: token('accent-700'),
          800: token('accent-800'),
          900: token('accent-900'),
        },
        verdict: {
          under: token('verdict-under'),
          fair: token('verdict-fair'),
          slight: token('verdict-slight'),
          high: token('verdict-high'),
          unknown: token('verdict-unknown'),
        },
        // Semantic status colours, so a warning is never a raw amber-50.
        warn: { fg: token('warn-fg'), bg: token('warn-bg'), border: token('warn-border') },
        danger: { fg: token('danger-fg'), bg: token('danger-bg'), border: token('danger-border') },
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        // A display step above Tailwind's scale, for the one number per screen
        // that is actually the answer.
        display: ['clamp(2.25rem, 1.6rem + 2.6vw, 3.25rem)', { lineHeight: '1.05', letterSpacing: '-0.028em' }],
        headline: ['clamp(1.75rem, 1.4rem + 1.5vw, 2.375rem)', { lineHeight: '1.12', letterSpacing: '-0.022em' }],
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        lift: 'var(--shadow-lift)',
        pop: 'var(--shadow-pop)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: { '0%': { opacity: '0.45' }, '50%': { opacity: '1' }, '100%': { opacity: '0.45' } },
        // The marker settles onto the axis rather than appearing on it, which
        // is what makes the reader look at where it landed.
        'marker-in': {
          '0%': { opacity: '0', transform: 'translateY(-6px) scale(0.94)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'band-in': { '0%': { transform: 'scaleX(0)' }, '100%': { transform: 'scaleX(1)' } },
      },
      animation: {
        'fade-up': 'fade-up 0.35s cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.6s ease-in-out infinite',
        'marker-in': 'marker-in 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.24s both',
        'band-in': 'band-in 0.55s cubic-bezier(0.16, 1, 0.3, 1) both',
      },
    },
  },
  plugins: [],
};

export default config;
