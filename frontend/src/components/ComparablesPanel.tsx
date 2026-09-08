'use client';

import { useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { currency, distance, monthsAgo, shortDate } from '@/lib/format';
import type { ComparableView } from '@/types/api';
import { Spinner } from './SearchForm';

/**
 * The comparables, with every retrieval score labelled for what it actually is.
 *
 * The `vector_similarity` column is the one most easily misread: it measures how
 * alike two listing *descriptions* are, not the probability that two properties
 * are worth the same. It is captioned accordingly, because a number presented
 * without its meaning is worse than no number.
 *
 * Include/Exclude is the human-in-the-loop control. Excluding records the
 * judgement; re-running recomputes the range, the label, the confidence and the
 * narrative — the assessment is never patched in place.
 */
export function ComparablesPanel({
  analysisId,
  comparables,
  onRerun,
  disabled,
}: {
  analysisId: string;
  comparables: ComparableView[];
  onRerun: () => void;
  disabled: boolean;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRejected, setShowRejected] = useState(false);

  const included = comparables.filter((item) => item.included);
  const rejected = comparables.filter((item) => !item.included);

  async function toggle(comparable: ComparableView) {
    setPending(comparable.id);
    setError(null);
    try {
      if (comparable.excluded_by_user) {
        await api.includeComparable(analysisId, comparable.id);
      } else {
        await api.excludeComparable(analysisId, comparable.id, 'Excluded by the user');
      }
      setDirty(true);
      onRerun();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not update this comparable.');
    } finally {
      setPending(null);
    }
  }

  async function rerun() {
    setRerunning(true);
    setError(null);
    try {
      await api.rerun(analysisId);
      setDirty(false);
      onRerun();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not re-run the analysis.');
    } finally {
      setRerunning(false);
    }
  }

  return (
    <section className="card card-pad">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
            Comparable sales
            <span className="chip bg-accent-100 tabular-nums text-accent-800">
              {included.length} used
            </span>
          </h2>
          <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-ink-500">
            These sales built the evidence range. If one does not belong — a different building, a
            renovation you know about, a distressed sale — exclude it and re-run.
          </p>
        </div>
        {dirty && (
          <button
            type="button"
            onClick={rerun}
            disabled={rerunning || disabled}
            className="btn-primary"
          >
            {rerunning ? (
              <>
                <Spinner /> Re-running
              </>
            ) : (
              'Re-run without excluded sales'
            )}
          </button>
        )}
      </div>

      {dirty && !rerunning && (
        <p className="mt-3.5 flex gap-2.5 rounded-lg border border-warn-border bg-warn-bg px-3.5 py-2.5 text-xs leading-relaxed text-warn-fg">
          <span aria-hidden className="mt-px shrink-0 font-bold">
            !
          </span>
          Your changes are recorded but the assessment above still reflects the previous set. Re-run
          to recompute the range, the verdict and the confidence.
        </p>
      )}
      {error && (
        <p
          role="alert"
          className="mt-3.5 rounded-lg border border-danger-border bg-danger-bg px-3.5 py-2.5 text-sm text-danger-fg"
        >
          {error}
        </p>
      )}

      <div className="mt-4 space-y-2.5">
        {included.map((comparable) => (
          <ComparableCard
            key={comparable.id}
            comparable={comparable}
            pending={pending === comparable.id}
            disabled={disabled || rerunning}
            onToggle={() => toggle(comparable)}
          />
        ))}
        {included.length === 0 && (
          <p className="rounded-lg border border-dashed border-ink-300 bg-surface-sunken px-4 py-6 text-center text-sm text-ink-600">
            No comparable sales were strong enough to include.
          </p>
        )}
      </div>

      {rejected.length > 0 && (
        <div className="mt-5 border-t border-ink-200 pt-4">
          <button
            type="button"
            onClick={() => setShowRejected((value) => !value)}
            aria-expanded={showRejected}
            className="group flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-ink-600 transition-colors hover:text-ink-900"
          >
            <span
              aria-hidden
              className={`text-ink-400 transition-transform duration-200 ${showRejected ? 'rotate-90' : ''}`}
            >
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M6 3.5 10.5 8 6 12.5" />
              </svg>
            </span>
            {showRejected ? 'Hide' : 'Show'} {rejected.length} candidate
            {rejected.length === 1 ? '' : 's'} that were considered but not used
          </button>
          {showRejected && (
            <div className="mt-3 space-y-2.5">
              {rejected.map((comparable) => (
                <ComparableCard
                  key={comparable.id}
                  comparable={comparable}
                  pending={pending === comparable.id}
                  disabled={disabled || rerunning}
                  onToggle={() => toggle(comparable)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ComparableCard({
  comparable,
  pending,
  disabled,
  onToggle,
}: {
  comparable: ComparableView;
  pending: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <article
      className={`overflow-hidden rounded-lg border transition-colors duration-200 ${
        comparable.included
          ? 'border-ink-200 bg-surface hover:border-ink-300'
          : 'border-dashed border-ink-300 bg-surface-sunken'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2.5 px-4 py-3.5">
        <div className="min-w-0 flex-1 basis-full sm:basis-auto">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span
              className={`text-lg font-bold tabular-nums tracking-tight ${
                comparable.included ? 'text-ink-950' : 'text-ink-500'
              }`}
            >
              {currency(comparable.sold_price)}
            </span>
            <span className="min-w-0 text-sm text-ink-600">{comparable.address}</span>
            {comparable.excluded_by_user && (
              <span className="chip bg-warn-bg text-warn-fg ring-1 ring-warn-border">
                excluded by you
              </span>
            )}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500">
            <Fact>
              Sold {shortDate(comparable.sold_at)}{' '}
              <span className="text-ink-400">({monthsAgo(comparable.sold_at)})</span>
            </Fact>
            <Fact>{distance(comparable.distance_km)} away</Fact>
            <Fact>
              {comparable.bedrooms ?? '?'} bed · {comparable.bathrooms ?? '?'} bath ·{' '}
              {comparable.carspaces ?? '?'} car
            </Fact>
            {comparable.floor_area_sqm && <Fact>{comparable.floor_area_sqm} m² internal</Fact>}
            {comparable.land_area_sqm && <Fact>{comparable.land_area_sqm} m² land</Fact>}
          </div>

          {!comparable.included && comparable.exclusion_reason && (
            <p className="mt-2 text-xs italic text-ink-500">{comparable.exclusion_reason}</p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="btn-ghost"
          >
            {open ? 'Less' : 'Details'}
          </button>
          <button
            type="button"
            onClick={onToggle}
            disabled={pending || disabled}
            className={
              comparable.excluded_by_user
                ? 'btn border border-accent-500 bg-surface px-3 py-1.5 text-xs text-accent-700 hover:bg-accent-50'
                : 'btn border border-ink-300 bg-surface px-3 py-1.5 text-xs text-ink-700 hover:border-ink-400 hover:bg-ink-100'
            }
          >
            {pending ? <Spinner /> : comparable.excluded_by_user ? 'Include' : 'Exclude'}
          </button>
        </div>
      </div>

      {open && (
        <div className="animate-fade-up space-y-4 border-t border-ink-200 bg-surface-sunken px-4 py-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {comparable.important_matches.length > 0 && (
              <div>
                <h4 className="label mb-2 text-accent-700">Shared with the target</h4>
                <ul className="space-y-1.5">
                  {comparable.important_matches.map((item) => (
                    <li key={item} className="flex gap-2 text-xs leading-relaxed text-ink-700">
                      <span aria-hidden className="font-bold text-accent-600">
                        +
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {comparable.important_differences.length > 0 && (
              <div>
                <h4 className="label mb-2 text-warn-fg">Differences</h4>
                <ul className="space-y-1.5">
                  {comparable.important_differences.map((item) => (
                    <li key={item} className="flex gap-2 text-xs leading-relaxed text-ink-700">
                      <span aria-hidden className="font-bold text-warn-fg">
                        −
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div>
            <h4 className="label mb-2">How this sale was found</h4>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Score
                label="Semantic similarity"
                value={comparable.vector_similarity}
                caption="how alike the two listing descriptions read — not a price relationship"
              />
              <Score
                label="Keyword match"
                value={comparable.keyword_score}
                caption="PostgreSQL full-text rank on explicit terms"
              />
              <Score
                label="Fusion score"
                value={comparable.fusion_score}
                caption="reciprocal rank fusion across all three retrieval arms"
                precision={4}
              />
              <Score
                label="Final relevance"
                value={comparable.rerank_score}
                caption="the grader's judgement of comparability, 0–1"
              />
            </div>
          </div>

          {comparable.description_excerpt && (
            <div>
              <h4 className="label mb-1.5">Listing text</h4>
              <p className="text-xs leading-relaxed text-ink-600">
                {comparable.description_excerpt}
              </p>
            </div>
          )}

          <p className="font-mono text-[11px] text-ink-400">
            Source: {comparable.source}
            {comparable.is_demo_data && ' · synthetic demonstration data'}
            {comparable.final_rank !== null && ` · ranked #${comparable.final_rank}`}
          </p>
        </div>
      )}
    </article>
  );
}

/** Facts read as a row of separate values, not one run-on sentence. */
function Fact({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-3 after:h-2.5 after:w-px after:bg-ink-200 last:after:hidden">
      <span>{children}</span>
    </span>
  );
}

function Score({
  label,
  value,
  caption,
  precision = 3,
}: {
  label: string;
  value: number | null;
  caption: string;
  precision?: number;
}) {
  return (
    <div className="rounded-md border border-ink-200 bg-surface px-2.5 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">{label}</p>
      <p className="mt-0.5 text-sm font-bold tabular-nums text-ink-900">
        {value === null ? '—' : value.toFixed(precision)}
      </p>
      <p className="mt-1 text-[10px] leading-snug text-ink-400">{caption}</p>
    </div>
  );
}
