import type {
  AnalysisDetail,
  AnalysisSummary,
  HealthResponse,
  TraceEvent,
} from '@/types/api';

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, '') ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      cache: 'no-store',
    });
  } catch {
    // A dead backend is the single most likely local failure; say so plainly
    // rather than surfacing the browser's opaque "Failed to fetch".
    throw new ApiError(`Cannot reach the analysis API at ${BASE_URL}. Is the backend running?`, 0);
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = 'The request was rejected as invalid.';
    } catch {
      /* keep the status-line fallback */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),

  createAnalysis: (query: string, askingPriceOverride?: number) =>
    request<{ id: string; status: string }>('/api/analysis', {
      method: 'POST',
      body: JSON.stringify(
        askingPriceOverride
          ? { query, asking_price_override: askingPriceOverride }
          : { query },
      ),
    }),

  getAnalysis: (id: string) => request<AnalysisDetail>(`/api/analysis/${id}`),

  listAnalyses: (limit = 8) => request<AnalysisSummary[]>(`/api/analysis?limit=${limit}`),

  getTrace: (id: string) => request<TraceEvent[]>(`/api/analysis/${id}/trace`),

  excludeComparable: (analysisId: string, comparableId: string, reason?: string) =>
    request<{ excluded: boolean; rerun_required: boolean }>(
      `/api/analysis/${analysisId}/comparables/${comparableId}/exclude`,
      { method: 'POST', body: JSON.stringify({ reason: reason ?? null }) },
    ),

  includeComparable: (analysisId: string, comparableId: string) =>
    request<{ excluded: boolean; rerun_required: boolean }>(
      `/api/analysis/${analysisId}/comparables/${comparableId}/include`,
      { method: 'POST' },
    ),

  rerun: (analysisId: string) =>
    request<{ id: string; status: string }>(`/api/analysis/${analysisId}/rerun`, {
      method: 'POST',
    }),
};

export { BASE_URL };
