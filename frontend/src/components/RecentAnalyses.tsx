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
      <h2 className="label mb-2.5">Recent analyses</h2>
      <ul className="divide-y divide-ink-200 overflow-hidden rounded-xl border border-ink-200 bg-white">
        {items.map((item) => {
          const label = item.assessment as Label | null;
          const style = label ? ASSESSMENT_STYLES[label] : null;
          return (
            <li key={item.id}>
              <Link
                href={`/analysis/${item.id}`}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 hover:bg-ink-50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink-900">
                    {item.address ?? item.query}
                  </span>
                  <span className="mt-0.5 block text-xs text-ink-500">
                    {shortDate(item.created_at)}
                    {item.asking_price ? ` · asking ${currency(item.asking_price)}` : ''}
                  </span>
                </span>
                {item.status !== 'complete' ? (
                  <span className="chip bg-ink-100 text-ink-600">{item.status}</span>
                ) : label && style ? (
                  <span className={`chip ${style.bg} ${style.text}`}>
                    {ASSESSMENT_LABELS[label]}
                    {item.confidence ? ` · ${item.confidence} confidence` : ''}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
