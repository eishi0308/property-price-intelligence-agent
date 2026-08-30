import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'Property Price Intelligence',
  description:
    'Evidence-based comparable-sales analysis for Australian residential property. Not a valuation.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-AU">
      <body className="flex min-h-screen flex-col antialiased">
        <header className="sticky top-0 z-40 border-b border-ink-200/70 bg-white/85 backdrop-blur-md">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3.5 sm:px-5">
            <Link href="/" className="group flex min-w-0 items-center gap-2.5">
              <span
                aria-hidden
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-800 text-[13px] font-bold text-white"
              >
                PI
              </span>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate text-sm font-semibold text-ink-900 group-hover:text-accent-800">
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
            <nav className="flex shrink-0 items-center gap-0.5 text-sm sm:gap-1">
              <Link
                href="/"
                className="rounded-lg px-2 py-1.5 text-ink-600 hover:bg-ink-100 hover:text-ink-900 sm:px-3"
              >
                Analyse
              </Link>
              <Link
                href="/how-it-works"
                className="rounded-lg px-2 py-1.5 text-ink-600 hover:bg-ink-100 hover:text-ink-900 sm:px-3"
              >
                <span className="sm:hidden">How</span>
                <span className="hidden sm:inline">How it works</span>
              </Link>
            </nav>
          </div>
        </header>

        {/* flex-1 pins the footer to the bottom of short pages instead of
            leaving it stranded mid-viewport. */}
        <main className="mx-auto w-full max-w-6xl flex-1 px-5 pb-20 pt-8">{children}</main>

        <footer className="mt-auto border-t border-ink-200/70 bg-white">
          <div className="mx-auto max-w-6xl px-5 py-7">
            <p className="text-xs leading-relaxed text-ink-500">
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
