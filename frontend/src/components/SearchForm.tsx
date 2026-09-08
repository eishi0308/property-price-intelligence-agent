'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api, ApiError } from '@/lib/api';

const EXAMPLES = [
  {
    label: '2-bed apartment · Strathfield',
    query: 'https://www.realestate.com.au/property-apartment-nsw-strathfield-145820394',
    hint: 'a realestate.com.au URL',
  },
  {
    label: '3-bed townhouse · Burwood',
    query: '5/8 Everton Road, Burwood NSW 2134',
    hint: 'a street address',
  },
  {
    label: '4-bed house · Concord',
    query: '27 Gladstone Street, Concord NSW 2137',
    hint: 'a thin market — watch the search widen',
  },
];

export function SearchForm() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [askingPrice, setAskingPrice] = useState('');
  const [showPrice, setShowPrice] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(value: string) {
    const trimmed = value.trim();
    if (trimmed.length < 4) {
      setError('Enter a property URL or an Australian street address.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const parsedPrice = askingPrice.replace(/[^0-9]/g, '');
      const { id } = await api.createAnalysis(
        trimmed,
        parsedPrice ? Number(parsedPrice) : undefined,
      );
      router.push(`/analysis/${id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not start the analysis.');
      setSubmitting(false);
    }
  }

  return (
    <div className="w-full">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(query);
        }}
        className="card card-pad shadow-lift"
      >
        <label htmlFor="query" className="label">
          Property URL or address
        </label>

        <div className="mt-2.5 flex flex-col gap-2.5 sm:flex-row">
          <div className="relative flex-1">
            <span
              aria-hidden
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-400"
            >
              <svg
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.7}
                strokeLinecap="round"
                className="h-[18px] w-[18px]"
              >
                <circle cx="9" cy="9" r="5.75" />
                <path d="m13.2 13.2 3.3 3.3" />
              </svg>
            </span>
            <input
              id="query"
              name="query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Paste a realestate.com.au or Domain link, or type an address"
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              /* 16px on mobile: anything smaller makes iOS Safari zoom the page
                 on focus and leave it zoomed. */
              className="field h-12 pl-11 text-base sm:text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="btn-primary h-12 shrink-0 sm:w-48"
          >
            {submitting ? (
              <>
                <Spinner /> Starting…
              </>
            ) : (
              'Analyse property'
            )}
          </button>
        </div>

        <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-2.5">
          <button
            type="button"
            onClick={() => setShowPrice((value) => !value)}
            aria-expanded={showPrice}
            className="cursor-pointer text-xs font-medium text-accent-700 underline underline-offset-[3px] transition-colors hover:text-accent-800"
          >
            {showPrice ? 'Use the listed price' : 'The listing shows no price, or a range'}
          </button>
          {showPrice && (
            <div className="flex animate-fade-up items-center gap-2">
              <div className="relative">
                <span
                  aria-hidden
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-ink-400"
                >
                  $
                </span>
                <input
                  aria-label="Asking price"
                  inputMode="numeric"
                  value={askingPrice}
                  onChange={(event) => setAskingPrice(event.target.value)}
                  placeholder="950000"
                  className="field w-36 py-1.5 pl-6 pr-2.5 text-sm tabular-nums"
                />
              </div>
              <span className="text-xs text-ink-500">the price you want tested</span>
            </div>
          )}
        </div>

        {error && (
          <p
            role="alert"
            className="mt-3.5 flex items-start gap-2 rounded-lg border border-danger-border bg-danger-bg px-3.5 py-2.5 text-sm text-danger-fg"
          >
            <span aria-hidden className="mt-[3px] shrink-0">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.7} className="h-3.5 w-3.5">
                <circle cx="8" cy="8" r="6.5" />
                <path d="M8 5v3.5" strokeLinecap="round" />
                <path d="M8 11h.008" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </span>
            {error}
          </p>
        )}
      </form>

      <div className="mt-5">
        <p className="label mb-2.5">Try one of these</p>
        <div className="grid gap-2.5 sm:grid-cols-3">
          {EXAMPLES.map((example) => (
            <button
              key={example.query}
              type="button"
              disabled={submitting}
              onClick={() => {
                setQuery(example.query);
                void submit(example.query);
              }}
              className="group cursor-pointer rounded-lg border border-ink-200 bg-surface px-4 py-3 text-left
                         transition-all duration-200 hover:-translate-y-px hover:border-accent-400
                         hover:shadow-lift disabled:cursor-not-allowed disabled:opacity-60
                         disabled:hover:translate-y-0 disabled:hover:border-ink-200 disabled:hover:shadow-none"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-ink-900">{example.label}</span>
                <span
                  aria-hidden
                  className="shrink-0 text-ink-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-accent-600"
                >
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                    <path d="M6 3.5 10.5 8 6 12.5" />
                  </svg>
                </span>
              </span>
              <span className="mt-0.5 block text-xs text-ink-500">{example.hint}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z"
      />
    </svg>
  );
}
