import type { Metadata, Viewport } from 'next';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import Link from 'next/link';
import { SiteNav } from '@/components/SiteNav';
import './globals.css';

/*
 * IBM Plex is the typeface of choice for financial and institutional reporting:
 * open apertures, unambiguous figures, and a mono cut that shares the sans's
 * proportions — so an address in mono and a heading in sans sit on the same
 * page without looking like two documents.
 */
const sans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Property Price Intelligence',
  description:
    'Evidence-based comparable-sales analysis for Australian residential property. Not a valuation.',
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fafbfc' },
    { media: '(prefers-color-scheme: dark)', color: '#0a0c10' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-AU" className={`${sans.variable} ${mono.variable}`}>
      <body className="flex min-h-dvh flex-col font-sans antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50
                     focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm
                     focus:font-semibold focus:text-ink-900 focus:shadow-pop"
        >
          Skip to content
        </a>

        <header className="sticky top-0 z-40 border-b border-ink-200 bg-ink-50/80 backdrop-blur-xl">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3.5 sm:px-5">
            <Link href="/" className="group flex min-w-0 items-center gap-2.5">
              <span
                aria-hidden
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px]
                           bg-[rgb(var(--accent-solid))] text-[13px] font-bold tracking-tight
                           text-[rgb(var(--on-accent))] shadow-card transition-transform
                           duration-200 group-hover:-translate-y-px"
              >
                PI
              </span>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate text-sm font-semibold tracking-tight text-ink-900">
                  Property Price Intelligence
                </span>
                {/* The tagline is the first thing to go on narrow screens: without
                    this the brand block and the nav together exceed a 390px viewport
                    and push the whole page sideways. */}
                <span className="hidden text-[11px] text-ink-500 sm:block">
                  Evidence, not estimates
                </span>
              </span>
            </Link>
            <SiteNav />
          </div>
        </header>

        {/* flex-1 pins the footer to the bottom of short pages instead of
            leaving it stranded mid-viewport. */}
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-8 sm:px-5">
          {children}
        </main>

        <footer className="mt-auto border-t border-ink-200 bg-surface">
          <div className="mx-auto max-w-6xl px-4 py-8 sm:px-5">
            <p className="max-w-4xl text-xs leading-relaxed text-ink-500">
              <strong className="font-semibold text-ink-700">Important.</strong> This is an
              evidence-based comparable-sales analysis, not a professional property valuation or
              financial advice. It compares an asking price against recent sales of similar
              properties and shows the evidence behind that comparison. It does not predict future
              prices, estimate investment returns, or replace a licensed valuer, a building
              inspection, or independent legal and financial advice.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
