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
        <div>
          <h2 className="text-sm font-semibold text-ink-900">
            Comparable sales{' '}
            <span className="font-normal text-ink-400">({included.length} used)</span>
          </h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-500">
            These sales built the evidence range. If one does not belong — a different building,
            a renovation you know about, a distressed sale — exclude it and re-run.
          </p>
        </div>
        {dirty && (
          <button type="button" onClick={rerun} disabled={rerunning || disabled} className="btn-primary">
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
        <p className="mt-3 rounded-lg bg-amber-50 px-3.5 py-2.5 text-xs leading-relaxed text-amber-900">
          Your changes are recorded but the assessment above still reflects the previous set.
          Re-run to recompute the range, the verdict and the confidence.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-800">
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
          <p className="rounded-lg bg-ink-50 px-3.5 py-3 text-sm text-ink-600">
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
            className="text-xs font-semibold text-ink-600 underline underline-offset-2 hover:text-ink-900"
          >
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
      className={`rounded-lg border transition ${
        comparable.included
          ? 'border-ink-200 bg-white'
          : 'border-dashed border-ink-200 bg-ink-50/60 opacity-90'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3.5">
        <div className="min-w-0 flex-1 basis-full sm:basis-auto">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="tnum text-lg font-semibold text-ink-900">
              {currency(comparable.sold_price)}
            </span>
            <span className="text-sm text-ink-600">{comparable.address}</span>
            {comparable.excluded_by_user && (
              <span className="chip bg-amber-100 text-amber-800">excluded by you</span>
            )}
          </div>

          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
            <span>
              Sold {shortDate(comparable.sold_at)}{' '}
              <span className="text-ink-400">({monthsAgo(comparable.sold_at)})</span>
            </span>
            <span>{distance(comparable.distance_km)} away</span>
            <span>
              {comparable.bedrooms ?? '?'} bed · {comparable.bathrooms ?? '?'} bath ·{' '}
              {comparable.carspaces ?? '?'} car
            </span>
            {comparable.floor_area_sqm && <span>{comparable.floor_area_sqm} m² internal</span>}
            {comparable.land_area_sqm && <span>{comparable.land_area_sqm} m² land</span>}
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
                ? 'btn border border-accent-600 bg-white px-3 py-1.5 text-xs text-accent-800 hover:bg-accent-50'
                : 'btn border border-ink-300 bg-white px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-50'
            }
          >
            {pending ? <Spinner /> : comparable.excluded_by_user ? 'Include' : 'Exclude'}
          </button>
        </div>
      </div>

      {open && (
        <div className="space-y-3.5 border-t border-ink-200 bg-ink-50/40 px-4 py-3.5">
          <div className="grid gap-3 sm:grid-cols-2">
            {comparable.important_matches.length > 0 && (
              <div>
                <h4 className="label mb-1.5 text-accent-800">Shared with the target</h4>
                <ul className="space-y-1">
                  {comparable.important_matches.map((item) => (
                    <li key={item} className="text-xs leading-relaxed text-ink-700">
                      + {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {comparable.important_differences.length > 0 && (
              <div>
                <h4 className="label mb-1.5 text-amber-700">Differences</h4>
                <ul className="space-y-1">
                  {comparable.important_differences.map((item) => (
                    <li key={item} className="text-xs leading-relaxed text-ink-700">
                      − {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div>
            <h4 className="label mb-1.5">How this sale was found</h4>
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

          <p className="text-[11px] text-ink-400">
            Source: {comparable.source}
            {comparable.is_demo_data && ' · synthetic demonstration data'}
            {comparable.final_rank !== null && ` · ranked #${comparable.final_rank}`}
          </p>
        </div>
      )}
    </article>
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
    <div className="rounded-md border border-ink-200 bg-white px-2.5 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">{label}</p>
      <p className="tnum mt-0.5 text-sm font-semibold text-ink-900">
        {value === null ? '—' : value.toFixed(precision)}
      </p>
      <p className="mt-1 text-[10px] leading-snug text-ink-400">{caption}</p>
    </div>
  );
}
