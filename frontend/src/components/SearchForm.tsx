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
        className="card card-pad"
      >
        <label htmlFor="query" className="label">
          Property URL or address
        </label>
        <div className="mt-2 flex flex-col gap-2.5 sm:flex-row">
          <input
            id="query"
            name="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Paste a realestate.com.au or Domain link, or type an address"
            autoComplete="off"
            spellCheck={false}
            disabled={submitting}
            className="w-full rounded-lg border border-ink-300 bg-white px-3.5 py-2.5 text-sm
                       text-ink-900 placeholder:text-ink-400 focus:border-accent-600
                       focus:outline-none focus:ring-2 focus:ring-accent-600/25 disabled:bg-ink-50"
          />
          <button type="submit" disabled={submitting} className="btn-primary shrink-0 sm:w-44">
            {submitting ? (
              <>
                <Spinner /> Starting…
              </>
            ) : (
              'Analyse property'
            )}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="button"
            onClick={() => setShowPrice((value) => !value)}
            className="text-xs font-medium text-accent-800 underline underline-offset-2 hover:text-accent-900"
          >
            {showPrice ? 'Use the listed price' : 'The listing shows no price, or a range'}
          </button>
          {showPrice && (
            <div className="flex items-center gap-2">
              <span aria-hidden className="text-sm text-ink-500">
                $
              </span>
              <input
                aria-label="Asking price"
                inputMode="numeric"
                value={askingPrice}
                onChange={(event) => setAskingPrice(event.target.value)}
                placeholder="950000"
                className="w-32 rounded-lg border border-ink-300 px-2.5 py-1.5 text-sm tnum
                           focus:border-accent-600 focus:outline-none focus:ring-2 focus:ring-accent-600/25"
              />
              <span className="text-xs text-ink-500">the price you want tested</span>
            </div>
          )}
        </div>

        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}
      </form>

      <div className="mt-4">
        <p className="label mb-2">Try one of these</p>
        <div className="grid gap-2 sm:grid-cols-3">
          {EXAMPLES.map((example) => (
            <button
              key={example.query}
              type="button"
              disabled={submitting}
              onClick={() => {
                setQuery(example.query);
                void submit(example.query);
              }}
              className="group rounded-lg border border-ink-200 bg-white px-3.5 py-3 text-left
                         transition hover:border-accent-400 hover:shadow-card disabled:opacity-60"
            >
              <span className="block text-sm font-medium text-ink-900 group-hover:text-accent-900">
                {example.label}
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
