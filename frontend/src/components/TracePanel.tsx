'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import type { RunMetadata, TraceEvent } from '@/types/api';
import { titleCase } from '@/lib/format';

const KIND_STYLES: Record<string, string> = {
  node: 'bg-ink-100 text-ink-700',
  tool_call: 'bg-accent-100 text-accent-800',
  retrieval: 'bg-blue-100 text-blue-800',
  rerank: 'bg-violet-100 text-violet-800',
  llm: 'bg-fuchsia-100 text-fuchsia-800',
  guardrail: 'bg-amber-100 text-amber-800',
};

/** The full run trace, always available — no third-party observability required. */
export function TracePanel({
  analysisId,
  metadata,
}: {
  analysisId: string;
  metadata: RunMetadata | null;
}) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && events === null) {
      setLoading(true);
      try {
        setEvents(await api.getTrace(analysisId));
      } catch {
        setEvents([]);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <section className="card card-pad">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">How this answer was produced</h2>
          <p className="mt-1 text-xs text-ink-500">
            Every node, tool call, retrieval and guardrail check from this run.
          </p>
        </div>
        <button type="button" onClick={toggle} aria-expanded={open} className="btn-secondary">
          {open ? 'Hide trace' : 'Show trace'}
        </button>
      </div>

      {metadata && (
        <dl className="mt-4 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <Meta term="Data provider" value={`${metadata.provider}${metadata.provider_is_demo ? ' (demo fixtures)' : ''}`} />
          <Meta term="Language model" value={metadata.llm_backend} />
          <Meta term="Embedding model" value={metadata.embedding_model} />
          <Meta term="Tool transport" value={`MCP over ${metadata.mcp_transport}`} />
          <Meta term="Search window" value={`${metadata.search_radius_km} km · ${metadata.lookback_months} months`} />
          <Meta
            term="Search widened"
            value={metadata.expansions_used === 0 ? 'not needed' : `${metadata.expansions_used}×`}
          />
          <Meta term="Total time" value={metadata.duration_ms ? `${metadata.duration_ms} ms` : '—'} />
          <Meta term="Tool calls" value={`${metadata.tool_calls.length}`} />
          <Meta
            term="Tracing"
            value={metadata.tracing_enabled ? 'LangSmith + local' : 'local trace only'}
          />
        </dl>
      )}

      {metadata?.mcp_degraded_reason && (
        <p className="mt-3 rounded-lg bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
          {metadata.mcp_degraded_reason}
        </p>
      )}

      {metadata && (
        <p className="mt-3 rounded-lg bg-ink-50 px-3.5 py-2.5 text-xs leading-relaxed text-ink-600">
          {metadata.vector_retrieval_quality}
        </p>
      )}

      {open && (
        <div className="mt-4 border-t border-ink-200 pt-4">
          {loading && <p className="animate-shimmer text-sm text-ink-500">Loading trace…</p>}
          {events && events.length === 0 && !loading && (
            <p className="text-sm text-ink-500">No trace events were recorded for this run.</p>
          )}
          {events && events.length > 0 && (
            <ol className="space-y-1.5">
              {events.map((event) => (
                <li
                  key={event.sequence}
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md px-2 py-1.5 odd:bg-ink-50/60"
                >
                  <span className="tnum w-6 shrink-0 text-right text-[11px] text-ink-400">
                    {event.sequence}
                  </span>
                  <span
                    className={`chip shrink-0 ${KIND_STYLES[event.kind] ?? 'bg-ink-100 text-ink-700'}`}
                  >
                    {titleCase(event.kind)}
                  </span>
                  <span className="font-mono text-xs text-ink-800">{event.name}</span>
                  {event.status !== 'ok' && (
                    <span className="chip bg-amber-100 text-amber-800">{event.status}</span>
                  )}
                  {event.duration_ms !== null && (
                    <span className="tnum text-[11px] text-ink-400">{event.duration_ms} ms</span>
                  )}
                  {Object.keys(event.detail).length > 0 && (
                    <span className="w-full pl-9 font-mono text-[11px] leading-relaxed text-ink-500">
                      {Object.entries(event.detail)
                        .map(([key, value]) => `${key}=${formatDetail(value)}`)
                        .join('  ')}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

function formatDetail(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function Meta({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="min-w-[6.5rem] shrink-0 font-semibold text-ink-500">{term}</dt>
      <dd className="font-mono text-[11px] leading-5 text-ink-800">{value}</dd>
    </div>
  );
}
