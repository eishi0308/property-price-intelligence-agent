'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { HealthResponse } from '@/types/api';

/**
 * Shows how the system is currently configured — including every way it is
 * degraded. Hiding this would be the easiest way to make the product look better
 * than it is, which is exactly what a tool people trust with a large financial
 * decision must not do.
 */
export function SystemStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
        <span className="font-semibold">Backend unreachable.</span> {error}
      </div>
    );
  }
  if (!health) {
    return <div className="h-11 animate-shimmer rounded-lg bg-ink-100" aria-hidden />;
  }

  const demo = health.provider.is_demo;

  return (
    <div
      className={`rounded-lg border px-4 py-3 text-sm ${
        demo ? 'border-amber-300/80 bg-amber-50 text-amber-900' : 'border-ink-200 bg-white text-ink-700'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2">
          <span
            aria-hidden
            className={`h-2 w-2 rounded-full ${demo ? 'bg-amber-500' : 'bg-accent-600'}`}
          />
          {demo ? (
            <span>
              <strong className="font-semibold">Demonstration data.</strong> Every property, price
              and sale date below is synthetic fixture data — realistic in shape, fabricated in
              content. It describes no real Australian property.
            </span>
          ) : (
            <span>
              Connected to <strong className="font-semibold">{health.provider.name}</strong> live
              property data.
            </span>
          )}
        </p>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold underline underline-offset-2 hover:bg-black/5"
          aria-expanded={open}
        >
          {open ? 'Hide' : 'System details'}
        </button>
      </div>

      {open && (
        <dl className="mt-3 grid gap-x-6 gap-y-2 border-t border-current/15 pt-3 text-xs sm:grid-cols-2">
          <Row term="Property provider" value={`${health.provider.name}${demo ? ' (fixtures)' : ''}`} />
          <Row term="Language model" value={health.llm_backend} />
          <Row term="Embeddings" value={`${health.embedding.model} (${health.embedding.backend})`} />
          <Row
            term="MCP transport"
            value={`${health.mcp.transport} — ${health.mcp.tools.length} tools across ${health.mcp.servers.length} servers`}
          />
          <Row term="Database" value={health.database ? 'PostgreSQL connected' : 'unreachable'} />
          <Row term="pgvector" value={health.pgvector ? 'available' : 'missing'} />
          <Row
            term="Tracing"
            value={health.tracing.langsmith_enabled ? 'LangSmith enabled' : 'local trace table only'}
          />
          {health.degradations.length > 0 && (
            <div className="sm:col-span-2">
              <dt className="label mb-1 text-current/70">Known limitations right now</dt>
              <ul className="list-disc space-y-1 pl-4">
                {health.degradations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="min-w-[7.5rem] shrink-0 font-semibold text-current/70">{term}</dt>
      <dd className="font-mono text-[11px] leading-5">{value}</dd>
    </div>
  );
}
