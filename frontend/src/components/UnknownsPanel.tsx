import type { AnalysisDetail } from '@/types/api';

/**
 * What the analysis could NOT establish. Given equal space to the findings on
 * purpose: for a buyer, "we could not check the strata report" is as
 * decision-relevant as anything in the evidence range.
 */
export function UnknownsPanel({ analysis }: { analysis: AnalysisDetail }) {
  const unknowns = new Set<string>([
    ...(analysis.assessment?.unknowns ?? []),
    ...analysis.missing_information,
  ]);
  if (unknowns.size === 0) return null;

  return (
    <section className="card card-pad">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
        <span aria-hidden className="text-ink-400">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-4 w-4">
            <circle cx="8" cy="8" r="6.25" />
            <path d="M6.5 6.4a1.55 1.55 0 1 1 2.05 1.47c-.4.15-.55.47-.55.87v.3" strokeLinecap="round" />
            <path d="M8 11.4h.008" strokeWidth="1.9" strokeLinecap="round" />
          </svg>
        </span>
        What we could not assess
      </h2>
      <p className="mt-1 text-xs text-ink-500">
        Information not available, or outside what a comparable-sales analysis can see.
      </p>
      <ul className="mt-3.5 space-y-2">
        {[...unknowns].map((item) => (
          <li key={item} className="flex gap-2.5 text-sm leading-relaxed text-ink-700">
            <span
              aria-hidden
              className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-ink-100 text-[10px] font-bold text-ink-500"
            >
              ?
            </span>
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
