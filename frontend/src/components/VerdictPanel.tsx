import type { AnalysisDetail } from '@/types/api';
import {
  ASSESSMENT_EXPLANATIONS,
  ASSESSMENT_LABELS,
  ASSESSMENT_STYLES,
  CONFIDENCE_EXPLANATIONS,
  currency,
  currencyCompact,
} from '@/lib/format';

/**
 * The headline answer. Four facts, in the order a buyer needs them: what is being
 * asked, what the evidence supports, the verdict, and how much weight the verdict
 * can bear. Confidence sits alongside the verdict rather than in fine print —
 * a "High" verdict on "Low" confidence is a different message entirely.
 */
export function VerdictPanel({ analysis }: { analysis: AnalysisDetail }) {
  const assessment = analysis.assessment;
  if (!assessment) return null;

  const style = ASSESSMENT_STYLES[assessment.assessment];
  const hasRange =
    assessment.evidence_range_low !== null && assessment.evidence_range_high !== null;

  return (
    <section className={`card overflow-hidden ${style.ring} ring-1`}>
      <div className={`${style.bg} px-5 py-5 sm:px-6`}>
        <div className="grid gap-5 min-[420px]:grid-cols-2 sm:gap-6 lg:grid-cols-4">
          <Figure label="Asking price">
            <span className="tnum text-2xl font-semibold text-ink-900">
              {currency(assessment.asking_price)}
            </span>
            {analysis.asking_price_source === 'user_supplied' && (
              <span className="mt-1 block text-[11px] text-ink-500">supplied by you</span>
            )}
          </Figure>

          <Figure label="Evidence range">
            {hasRange ? (
              <>
                <span className="tnum text-xl font-semibold text-ink-900 sm:text-2xl">
                  {currencyCompact(assessment.evidence_range_low)} –{' '}
                  {currencyCompact(assessment.evidence_range_high)}
                </span>
                <span className="mt-1 block text-[11px] text-ink-500">
                  median {currency(assessment.evidence_median)}
                </span>
              </>
            ) : (
              <span className="text-2xl font-semibold text-ink-400">—</span>
            )}
          </Figure>

          <Figure label="Assessment">
            <span className={`text-2xl font-semibold ${style.text}`}>
              {ASSESSMENT_LABELS[assessment.assessment]}
            </span>
          </Figure>

          <Figure label="Confidence">
            <span className="text-2xl font-semibold capitalize text-ink-900">
              {assessment.confidence}
            </span>
            <span className="mt-1 block text-[11px] text-ink-500">
              evidence rated {assessment.evidence_quality}
            </span>
          </Figure>
        </div>
      </div>

      <div className="card-pad space-y-4 border-t border-ink-200/70">
        <p className="text-sm leading-relaxed text-ink-700">
          {ASSESSMENT_EXPLANATIONS[assessment.assessment]}
        </p>

        {assessment.reasoning_summary && (
          <div>
            <h3 className="label mb-1.5">Why</h3>
            <p className="text-[15px] leading-relaxed text-ink-800">
              {assessment.reasoning_summary}
            </p>
          </div>
        )}

        {assessment.important_differences.length > 0 && (
          <div>
            <h3 className="label mb-1.5">Differences worth weighing</h3>
            <ul className="space-y-1.5">
              {assessment.important_differences.map((item) => (
                <li key={item} className="flex gap-2 text-sm text-ink-700">
                  <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-400" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-lg bg-ink-50 px-3.5 py-3 text-xs leading-relaxed text-ink-600">
          <strong className="font-semibold text-ink-700">Confidence: {assessment.confidence}.</strong>{' '}
          {CONFIDENCE_EXPLANATIONS[assessment.confidence]}
          {assessment.guardrail_notes.length > 0 && (
            <ul className="mt-2 space-y-1">
              {assessment.guardrail_notes.map((note) => (
                <li key={note}>• {note}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="label">{label}</p>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}
