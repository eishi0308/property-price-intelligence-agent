import type { AnalysisDetail } from '@/types/api';
import {
  ASSESSMENT_EXPLANATIONS,
  ASSESSMENT_LABELS,
  ASSESSMENT_STYLES,
  CONFIDENCE_EXPLANATIONS,
  currency,
  currencyCompact,
} from '@/lib/format';
import { PriceRangeChart } from './PriceRangeChart';

/**
 * The headline answer.
 *
 * The verdict is the largest thing on the page because it is the thing the
 * reader came for, and confidence is set immediately beside it rather than in
 * fine print — "High" on "Low confidence" is a different message entirely, and
 * separating the two invites reading only the first half.
 *
 * Below it the same judgement is shown as geometry, then as the four numbers it
 * was computed from. Each restatement is a check on the last.
 */
export function VerdictPanel({ analysis }: { analysis: AnalysisDetail }) {
  const assessment = analysis.assessment;
  if (!assessment) return null;

  const style = ASSESSMENT_STYLES[assessment.assessment];
  const hasRange =
    assessment.evidence_range_low !== null && assessment.evidence_range_high !== null;
  const usedCount = analysis.comparables.filter((item) => item.included).length;

  return (
    <section className={`card overflow-hidden ring-1 ${style.ring}`} aria-labelledby="verdict">
      <div className={`relative ${style.bg} px-5 pb-6 pt-5 sm:px-7 sm:pb-7 sm:pt-6`}>
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <p className="label">Assessment</p>
            <h2
              id="verdict"
              className={`mt-1.5 text-headline font-bold ${style.text}`}
            >
              {ASSESSMENT_LABELS[assessment.assessment]}
            </h2>
          </div>
          <ConfidenceBadge level={assessment.confidence} quality={assessment.evidence_quality} />
        </div>

        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-ink-700">
          {ASSESSMENT_EXPLANATIONS[assessment.assessment]}
        </p>

        {hasRange && (
          <PriceRangeChart
            askingPrice={assessment.asking_price}
            low={assessment.evidence_range_low}
            high={assessment.evidence_range_high}
            median={assessment.evidence_median}
            sales={analysis.comparables.map((item) => ({
              id: item.id,
              price: item.sold_price,
              included: item.included,
            }))}
            tone={style}
            verdictLabel={ASSESSMENT_LABELS[assessment.assessment]}
          />
        )}
      </div>

      {/* gap-px over an ink background draws the hairlines, so the grid stays
          correctly ruled however it wraps. */}
      <dl className="grid grid-cols-2 gap-px border-y border-ink-200 bg-ink-200 sm:grid-cols-4">
        <Figure label="Asking price" note={analysis.asking_price_source === 'user_supplied' ? 'supplied by you' : undefined}>
          {currency(assessment.asking_price)}
        </Figure>
        <Figure label="Evidence range" note={hasRange ? 'middle 50% of sales' : undefined}>
          {hasRange
            ? `${currencyCompact(assessment.evidence_range_low)} – ${currencyCompact(assessment.evidence_range_high)}`
            : '—'}
        </Figure>
        <Figure label="Median sale" note={hasRange ? 'of the comparables used' : undefined}>
          {currency(assessment.evidence_median)}
        </Figure>
        <Figure label="Comparables used" note="after grading">
          {usedCount}
        </Figure>
      </dl>

      <div className="card-pad space-y-5">
        {assessment.reasoning_summary && (
          <div>
            <h3 className="label mb-1.5">Why</h3>
            <p className="max-w-3xl text-[15px] leading-relaxed text-ink-800">
              {assessment.reasoning_summary}
            </p>
          </div>
        )}

        {assessment.important_differences.length > 0 && (
          <div>
            <h3 className="label mb-2">Differences worth weighing</h3>
            <ul className="max-w-3xl space-y-1.5">
              {assessment.important_differences.map((item) => (
                <li key={item} className="flex gap-2.5 text-sm leading-relaxed text-ink-700">
                  <span
                    aria-hidden
                    className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-400"
                  />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-lg border border-ink-200 bg-surface-sunken px-4 py-3 text-xs leading-relaxed text-ink-600">
          <strong className="font-semibold capitalize text-ink-800">
            {assessment.confidence} confidence.
          </strong>{' '}
          {CONFIDENCE_EXPLANATIONS[assessment.confidence]}
          {assessment.guardrail_notes.length > 0 && (
            <ul className="mt-2 space-y-1">
              {assessment.guardrail_notes.map((note) => (
                <li key={note} className="flex gap-2">
                  <span aria-hidden className="text-ink-400">
                    ·
                  </span>
                  {note}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Confidence is drawn as three bars as well as named. Colour alone would fail a
 * colourblind reader; a shape that fills up would not survive translation. Bars
 * plus the word survive both.
 */
function ConfidenceBadge({ level, quality }: { level: 'low' | 'medium' | 'high'; quality: string }) {
  const filled = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-ink-200 bg-surface px-3 py-2 shadow-card">
      <span aria-hidden className="flex items-end gap-[3px]">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className={`w-[3px] rounded-full ${
              index < filled ? 'bg-[rgb(var(--accent-solid))]' : 'bg-ink-300'
            }`}
            style={{ height: `${7 + index * 4}px` }}
          />
        ))}
      </span>
      <span className="leading-tight">
        <span className="block text-xs font-semibold capitalize text-ink-900">
          {level} confidence
        </span>
        <span className="block text-[11px] text-ink-500">evidence rated {quality}</span>
      </span>
    </div>
  );
}

function Figure({
  label,
  note,
  children,
}: {
  label: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface px-5 py-4">
      <dt className="label">{label}</dt>
      <dd className="mt-1.5 text-lg font-semibold tabular-nums tracking-tight text-ink-900">
        {children}
      </dd>
      {note && <p className="mt-0.5 text-[11px] text-ink-500">{note}</p>}
    </div>
  );
}
