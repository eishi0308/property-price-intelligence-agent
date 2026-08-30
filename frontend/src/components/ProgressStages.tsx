'use client';

import type { StageProgress } from '@/types/api';
import { Spinner } from './SearchForm';

/**
 * The real workflow, as it happens. Every line corresponds to a node that
 * actually executed in the agent graph — including the loop where the search is
 * widened, which is the most interesting thing the agent does and would be
 * invisible in a generic progress bar.
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
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-900">
          {running ? 'Analysing' : 'What the agent did'}
        </h2>
        {running && (
          <span className="flex items-center gap-2 text-xs text-ink-500">
            <Spinner /> working
          </span>
        )}
      </div>

      <ol className="mt-4 space-y-2.5">
        {stages.length === 0 && running && (
          <li className="flex items-center gap-3 text-sm text-ink-500">
            <span className="h-4 w-4 animate-shimmer rounded-full bg-ink-200" aria-hidden />
            Resolving the property…
          </li>
        )}
        {stages.map((stage, index) => (
          <li key={`${stage.stage}-${index}`} className="flex animate-fade-up items-start gap-3">
            <StageIcon ok={stage.ok} />
            <div className="min-w-0 flex-1">
              <p className={`text-sm font-medium ${stage.ok ? 'text-ink-900' : 'text-amber-800'}`}>
                {stage.label}
              </p>
              {stage.detail && (
                <p className="mt-0.5 break-words text-xs leading-relaxed text-ink-500">
                  {stage.detail}
                </p>
              )}
            </div>
          </li>
        ))}
        {running && stages.length > 0 && (
          <li className="flex items-center gap-3 pl-0.5 text-sm text-ink-400">
            <span className="h-4 w-4 animate-shimmer rounded-full bg-ink-200" aria-hidden />
            <span className="animate-shimmer">continuing…</span>
          </li>
        )}
      </ol>
    </section>
  );
}

function StageIcon({ ok }: { ok: boolean }) {
  if (!ok) {
    return (
      <span
        aria-label="attention"
        className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[10px] font-bold text-amber-700"
      >
        !
      </span>
    );
  }
  return (
    <span
      aria-label="done"
      className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-100 text-accent-800"
    >
      <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" aria-hidden>
        <path
          d="M2 6.2 4.6 8.8 10 3.4"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
