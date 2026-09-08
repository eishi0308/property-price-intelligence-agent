'use client';

import type { StageProgress } from '@/types/api';
import { Spinner } from './SearchForm';

/**
 * The real workflow, as it happens. Every line corresponds to a node that
 * actually executed in the agent graph — including the loop where the search is
 * widened, which is the most interesting thing the agent does and would be
 * invisible in a generic progress bar.
 *
 * Drawn as a connected rail rather than a list: the connection is what says
 * "these steps followed one another", which is the claim being made.
 */
export function ProgressStages({
  stages,
  running,
}: {
  stages: StageProgress[];
  running: boolean;
}) {
  return (
    <section className="card card-pad" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-900">
          {running ? 'Analysing' : 'What the agent did'}
        </h2>
        {running && (
          <span className="flex items-center gap-2 text-xs font-medium text-ink-500">
            <Spinner /> working
          </span>
        )}
      </div>

      <ol className="relative mt-4">
        {/* The rail sits behind the markers and stops short of the last one, so
            a finished run does not trail off into nothing. */}
        <span
          aria-hidden
          className="absolute bottom-4 left-[7px] top-2 w-px bg-ink-200"
          style={{ display: stages.length > 1 || running ? undefined : 'none' }}
        />

        {stages.length === 0 && running && (
          <li className="relative flex items-center gap-3 pb-1 pl-6 text-sm text-ink-500">
            <PendingMarker />
            Resolving the property…
          </li>
        )}

        {stages.map((stage, index) => (
          <li
            key={`${stage.stage}-${index}`}
            className="relative animate-fade-up pb-3.5 pl-6 last:pb-0"
            style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
          >
            <StageMarker ok={stage.ok} />
            <p
              className={`text-sm font-medium ${stage.ok ? 'text-ink-900' : 'text-warn-fg'}`}
            >
              {stage.label}
            </p>
            {stage.detail && (
              <p className="mt-0.5 break-words text-xs leading-relaxed text-ink-500">
                {stage.detail}
              </p>
            )}
          </li>
        ))}

        {running && stages.length > 0 && (
          <li className="relative flex items-center gap-3 pl-6 pt-1 text-sm text-ink-400">
            <PendingMarker />
            <span className="animate-shimmer">continuing…</span>
          </li>
        )}
      </ol>
    </section>
  );
}

function PendingMarker() {
  return (
    <span
      aria-hidden
      className="absolute left-0 top-1 h-4 w-4 animate-shimmer rounded-full bg-ink-200 ring-4 ring-surface"
    />
  );
}

function StageMarker({ ok }: { ok: boolean }) {
  if (!ok) {
    return (
      <span
        aria-label="attention"
        role="img"
        className="absolute left-0 top-0.5 flex h-4 w-4 items-center justify-center rounded-full
                   bg-warn-bg text-[10px] font-bold text-warn-fg ring-4 ring-surface"
      >
        !
      </span>
    );
  }
  return (
    <span
      aria-label="done"
      role="img"
      className="absolute left-0 top-0.5 flex h-4 w-4 items-center justify-center rounded-full
                 bg-accent-100 text-accent-700 ring-4 ring-surface"
    >
      <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" aria-hidden>
        <path
          d="M2 6.2 4.6 8.8 10 3.4"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
