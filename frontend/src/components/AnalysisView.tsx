'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import type { AnalysisDetail } from '@/types/api';
import { ComparablesPanel } from './ComparablesPanel';
import { EvidencePanel } from './EvidencePanel';
import { ProgressStages } from './ProgressStages';
import { TargetSummary } from './TargetSummary';
import { TracePanel } from './TracePanel';
import { UnknownsPanel } from './UnknownsPanel';
import { VerdictPanel } from './VerdictPanel';

const POLL_INTERVAL_MS = 700;
const POLL_TIMEOUT_MS = 180_000;

export function AnalysisView({ analysisId }: { analysisId: string }) {
  const [analysis, setAnalysis] = useState<AnalysisDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef(Date.now());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const detail = await api.getAnalysis(analysisId);
      setAnalysis(detail);
      setError(null);

      const running = detail.status === 'pending' || detail.status === 'running';
      if (running && Date.now() - startedAt.current < POLL_TIMEOUT_MS) {
        timer.current = setTimeout(() => void poll(), POLL_INTERVAL_MS);
      } else if (running) {
        setError('This analysis is taking unusually long. The backend may have stalled.');
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not load this analysis.');
    }
  }, [analysisId]);

  useEffect(() => {
    startedAt.current = Date.now();
    void poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  const restartPolling = useCallback(() => {
    startedAt.current = Date.now();
    if (timer.current) clearTimeout(timer.current);
    void poll();
  }, [poll]);

  if (error && !analysis) {
    return (
      <ErrorState message={error} />
    );
  }

  if (!analysis) {
    // Shaped like the finished page rather than two grey slabs: the layout does
    // not jump when the data lands, and the wait already tells you what is coming.
    return (
      <div className="space-y-5" aria-busy="true" aria-label="Loading analysis">
        <div className="h-32 animate-shimmer rounded-xl bg-ink-100" />
        <div className="h-72 animate-shimmer rounded-xl bg-ink-100" />
        <div className="grid gap-5 lg:grid-cols-5">
          <div className="h-64 animate-shimmer rounded-xl bg-ink-100 lg:col-span-3" />
          <div className="h-64 animate-shimmer rounded-xl bg-ink-100 lg:col-span-2" />
        </div>
      </div>
    );
  }

  const running = analysis.status === 'pending' || analysis.status === 'running';
  const failed = analysis.status === 'failed';

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-xs text-ink-500">
        <Link
          href="/"
          className="shrink-0 rounded font-medium transition-colors hover:text-ink-900 hover:underline"
        >
          Analyse
        </Link>
        <span aria-hidden className="shrink-0 text-ink-300">
          /
        </span>
        {/* min-w-0 is what actually lets `truncate` engage inside a flex row;
            without it the long query string forces the page wider than the viewport. */}
        <span className="min-w-0 truncate font-mono">{analysis.query}</span>
      </nav>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-warn-border bg-warn-bg px-4 py-3 text-sm text-warn-fg"
        >
          {error}
        </p>
      )}

      {analysis.target_property && (
        <TargetSummary
          property={analysis.target_property}
          description={analysis.target_description}
        />
      )}

      {(running || !analysis.assessment) && (
        <ProgressStages stages={analysis.stages} running={running} />
      )}

      {failed && analysis.error && (
        <section className="card card-pad border-danger-border bg-danger-bg">
          <h2 className="text-base font-semibold text-danger-fg">The analysis could not complete</h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-danger-fg">{analysis.error}</p>
          <Link href="/" className="btn-secondary mt-3.5">
            Try another property
          </Link>
        </section>
      )}

      {analysis.assessment && !running && (
        <>
          <VerdictPanel analysis={analysis} />

          {/* `min-w-0` on both columns is essential: a grid item defaults to
              `min-width: auto`, so without it the comparable cards' min-content
              width (~540px) forces the whole page wider than a phone viewport. */}
          <div className="grid gap-5 lg:grid-cols-5">
            <div className="min-w-0 lg:col-span-3">
              <ComparablesPanel
                analysisId={analysis.id}
                comparables={analysis.comparables}
                onRerun={restartPolling}
                disabled={running}
              />
            </div>
            <div className="min-w-0 space-y-5 lg:col-span-2">
              <UnknownsPanel analysis={analysis} />
              <EvidencePanel evidence={analysis.evidence} />
            </div>
          </div>

          <details className="card group card-pad">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-ink-900">
              <span
                aria-hidden
                className="text-ink-400 transition-transform duration-200 group-open:rotate-90"
              >
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                  <path d="M6 3.5 10.5 8 6 12.5" />
                </svg>
              </span>
              What the agent did, step by step
            </summary>
            <div className="mt-4">
              <ProgressStages stages={analysis.stages} running={false} />
            </div>
          </details>

          <TracePanel analysisId={analysis.id} metadata={analysis.metadata} />

          <p className="px-1 text-xs leading-relaxed text-ink-500">{analysis.disclaimer}</p>
        </>
      )}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="card card-pad max-w-xl">
      <h1 className="text-lg font-semibold text-ink-900">Could not load this analysis</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-600">{message}</p>
      <Link href="/" className="btn-primary mt-4">
        Start a new analysis
      </Link>
    </div>
  );
}
