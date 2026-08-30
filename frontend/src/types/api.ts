/**
 * Types mirroring the FastAPI Pydantic schemas.
 *
 * Kept hand-written rather than generated so the fields the UI depends on are
 * explicit; every one of them is asserted in the backend's API tests.
 */

export type AssessmentLabel =
  | 'underpriced'
  | 'fair'
  | 'slightly_high'
  | 'high'
  | 'insufficient_evidence';

export type ConfidenceLevel = 'low' | 'medium' | 'high';
export type EvidenceQuality = 'insufficient' | 'weak' | 'adequate' | 'strong';
export type AnalysisStatus = 'pending' | 'running' | 'complete' | 'failed';

export interface Coordinates {
  latitude: number;
  longitude: number;
}

export interface PropertyRecord {
  external_id: string;
  provider: string;
  address: string;
  suburb: string;
  state: string;
  postcode: string;
  coordinates: Coordinates | null;
  property_type: string;
  bedrooms: number | null;
  bathrooms: number | null;
  carspaces: number | null;
  floor_area_sqm: number | null;
  land_area_sqm: number | null;
  year_built: number | null;
  is_demo_data: boolean;
}

export interface ComparableCitation {
  external_id: string;
  address: string;
  sold_price: number;
  sold_at: string;
  distance_km: number | null;
  relevance: number;
}

export interface PriceAssessment {
  assessment: AssessmentLabel;
  asking_price: number | null;
  evidence_range_low: number | null;
  evidence_range_high: number | null;
  evidence_median: number | null;
  confidence: ConfidenceLevel;
  reasoning_summary: string;
  supporting_comparables: ComparableCitation[];
  important_differences: string[];
  unknowns: string[];
  citations: string[];
  generated_by: string;
  evidence_quality: EvidenceQuality;
  guardrail_notes: string[];
  is_demo_data: boolean;
}

export interface ComparableView {
  id: string;
  external_id: string;
  address: string;
  suburb: string;
  sold_price: number;
  sold_at: string;
  distance_km: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  carspaces: number | null;
  floor_area_sqm: number | null;
  land_area_sqm: number | null;
  property_type: string;
  vector_similarity: number | null;
  keyword_score: number | null;
  fusion_score: number | null;
  rerank_score: number | null;
  final_rank: number | null;
  important_matches: string[];
  important_differences: string[];
  included: boolean;
  exclusion_reason: string | null;
  excluded_by_user: boolean;
  source: string;
  source_url: string | null;
  description_excerpt: string | null;
  is_demo_data: boolean;
}

export interface EvidenceView {
  id: string;
  source_type: string;
  title: string;
  content: string;
  why_it_matters: string;
  source: string;
  source_url: string | null;
  published_at: string | null;
  similarity: number | null;
  is_demo_data: boolean;
}

export interface StageProgress {
  stage: string;
  label: string;
  detail: string | null;
  completed_at: string | null;
  ok: boolean;
}

export interface ToolCall {
  tool: string;
  transport: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  duration_ms: number | null;
  error: string | null;
}

export interface RunMetadata {
  provider: string;
  provider_is_demo: boolean;
  llm_backend: string;
  embedding_backend: string;
  embedding_model: string;
  tracing_enabled: boolean;
  vector_retrieval_quality: string;
  search_radius_km: number;
  lookback_months: number;
  expansions_used: number;
  duration_ms: number | null;
  mcp_transport: string;
  mcp_degraded_reason: string | null;
  tool_calls: ToolCall[];
}

export interface AnalysisDetail {
  id: string;
  status: AnalysisStatus;
  query: string;
  created_at: string;
  target_property: PropertyRecord | null;
  target_description: string | null;
  asking_price: number | null;
  asking_price_source: string | null;
  assessment: PriceAssessment | null;
  comparables: ComparableView[];
  evidence: EvidenceView[];
  stages: StageProgress[];
  evidence_quality: EvidenceQuality | null;
  missing_information: string[];
  metadata: RunMetadata | null;
  error: string | null;
  disclaimer: string;
}

export interface AnalysisSummary {
  id: string;
  status: AnalysisStatus;
  query: string;
  created_at: string;
  address: string | null;
  assessment: string | null;
  confidence: string | null;
  asking_price: number | null;
}

export interface TraceEvent {
  sequence: number;
  kind: string;
  name: string;
  status: string;
  duration_ms: number | null;
  detail: Record<string, unknown>;
  at: string | null;
}

export interface HealthResponse {
  status: string;
  database: boolean;
  pgvector: boolean;
  provider: { name: string; is_demo: boolean; supports_semantic_retrieval: boolean };
  llm_backend: string;
  embedding: { backend: string; model: string; note: string };
  mcp: {
    transport: string;
    servers: string[];
    tools: string[];
    degraded_reason: string | null;
  };
  tracing: { langsmith_enabled: boolean; project: string | null; local_trace_table: string };
  degradations: string[];
  disclaimer: string;
}
