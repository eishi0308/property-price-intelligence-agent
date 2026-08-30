import type { Config } from 'tailwindcss';

/**
 * A restrained palette. This product asks people to trust it with a six-figure
 * decision, so the design language is closer to a professional report than a
 * consumer app: ink and slate for text, a single deep teal accent, and verdict
 * colours used sparingly and only where they carry meaning.
 */
const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9', 100: '#eceef2', 200: '#d5dae2', 300: '#b0b9c8',
          400: '#8593a8', 500: '#66758c', 600: '#515e73', 700: '#434d5e',
          800: '#3a4250', 900: '#343a45', 950: '#22262e',
        },
        accent: {
          50: '#eefbf7', 100: '#d5f5eb', 200: '#aeead8', 300: '#79d9c1',
          400: '#45c0a5', 500: '#1fa48b', 600: '#128471', 700: '#106a5c',
          800: '#11554b', 900: '#11473f', 950: '#032926',
        },
        verdict: {
          under: '#0f766e',
          fair: '#15803d',
          slight: '#b45309',
          high: '#b91c1c',
          unknown: '#525b6b',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)',
        lift: '0 4px 6px -1px rgba(16, 24, 40, 0.06), 0 12px 24px -4px rgba(16, 24, 40, 0.08)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: { '0%': { opacity: '0.45' }, '50%': { opacity: '1' }, '100%': { opacity: '0.45' } },
      },
      animation: {
        'fade-up': 'fade-up 0.35s ease-out both',
        shimmer: 'shimmer 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

export default config;
