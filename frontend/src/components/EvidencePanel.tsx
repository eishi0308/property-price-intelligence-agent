'use client';

import { useState } from 'react';
import type { EvidenceView } from '@/types/api';
import { shortDate, titleCase } from '@/lib/format';

const DOMAIN_ORDER = [
  'property_summary',
  'listing_description',
  'market_context',
  'location_context',
];

const DOMAIN_LABELS: Record<string, string> = {
  property_summary: 'The property itself',
  listing_description: 'Comparable sale listings',
  market_context: 'Suburb market context',
  location_context: 'Location context',
};

/** Every source the assessment was allowed to draw on, grouped by retrieval domain. */
export function EvidencePanel({ evidence }: { evidence: EvidenceView[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (evidence.length === 0) return null;

  const grouped = new Map<string, EvidenceView[]>();
  for (const item of evidence) {
    grouped.set(item.source_type, [...(grouped.get(item.source_type) ?? []), item]);
  }
  // Fixed narrative order: the property, then its comparables, then the wider
  // market. Unrecognised domains sort last rather than disappearing.
  const rank = (domain: string) => {
    const index = DOMAIN_ORDER.indexOf(domain);
    return index === -1 ? DOMAIN_ORDER.length : index;
  };
  const domains = [...grouped.keys()].sort((a, b) => rank(a) - rank(b));

  return (
    <section className="card card-pad">
      <h2 className="text-sm font-semibold text-ink-900">Evidence used</h2>
      <p className="mt-1 text-xs text-ink-500">
        The assessment above was written from these sources and nothing else. Anything not shown
        here was not available to it.
      </p>

      <div className="mt-4 space-y-5">
        {domains.map((domain) => (
          <div key={domain}>
            <h3 className="label mb-2">
              {DOMAIN_LABELS[domain] ?? titleCase(domain)}{' '}
              <span className="font-normal normal-case tracking-normal text-ink-400">
                ({grouped.get(domain)?.length})
              </span>
            </h3>
            <ul className="space-y-2">
              {(grouped.get(domain) ?? []).map((item) => {
                const isOpen = expanded === item.id;
                return (
                  <li key={item.id} className="rounded-lg border border-ink-200 bg-ink-50/50">
                    <button
                      type="button"
                      onClick={() => setExpanded(isOpen ? null : item.id)}
                      aria-expanded={isOpen}
                      className="flex w-full items-start justify-between gap-3 px-3.5 py-2.5 text-left"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-ink-900">
                          {item.title}
                        </span>
                        <span className="mt-0.5 block text-xs text-ink-500">
                          {item.source}
                          {item.published_at ? ` · ${shortDate(item.published_at)}` : ''}
                          {item.similarity !== null
                            ? ` · semantic similarity ${item.similarity.toFixed(2)}`
                            : ''}
                        </span>
                      </span>
                      <span className="shrink-0 pt-0.5 text-xs text-ink-500">
                        {isOpen ? 'Hide' : 'Read'}
                      </span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-ink-200 px-3.5 py-3">
                        <p className="text-sm leading-relaxed text-ink-700">{item.content}</p>
                        <p className="mt-2.5 border-l-2 border-accent-300 pl-3 text-xs italic leading-relaxed text-ink-500">
                          Why this matters: {item.why_it_matters}
                        </p>
                        {item.is_demo_data && (
                          <p className="mt-2 text-[11px] font-medium text-amber-700">
                            Synthetic demonstration data.
                          </p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
