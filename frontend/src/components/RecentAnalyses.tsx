'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { ASSESSMENT_LABELS, ASSESSMENT_STYLES, currency, shortDate } from '@/lib/format';
import type { AnalysisSummary } from '@/types/api';

type Label = keyof typeof ASSESSMENT_LABELS;

export function RecentAnalyses() {
  const [items, setItems] = useState<AnalysisSummary[] | null>(null);

  useEffect(() => {
    api
      .listAnalyses(6)
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  if (!items || items.length === 0) return null;

  return (
    <section>
      <h2 className="label mb-3">Recent analyses</h2>
      <ul className="divide-hair overflow-hidden rounded-xl border border-ink-200 bg-surface shadow-card">
        {items.map((item) => {
          const label = item.assessment as Label | null;
          const style = label ? ASSESSMENT_STYLES[label] : null;
          return (
            <li key={item.id}>
              <Link
                href={`/analysis/${item.id}`}
                className="group flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3.5 transition-colors hover:bg-surface-sunken"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink-900">
                    {item.address ?? item.query}
                  </span>
                  <span className="mt-0.5 block text-xs tabular-nums text-ink-500">
                    {shortDate(item.created_at)}
                    {item.asking_price ? ` · asking ${currency(item.asking_price)}` : ''}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2.5">
                  {item.status !== 'complete' ? (
                    <span className="chip bg-ink-100 text-ink-600">{item.status}</span>
                  ) : label && style ? (
                    <span className={`chip ${style.bg} ${style.text}`}>
                      {ASSESSMENT_LABELS[label]}
                      {item.confidence ? ` · ${item.confidence} confidence` : ''}
                    </span>
                  ) : null}
                  <span
                    aria-hidden
                    className="text-ink-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-ink-500"
                  >
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                      <path d="M6 3.5 10.5 8 6 12.5" />
                    </svg>
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
