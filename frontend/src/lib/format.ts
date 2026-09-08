import type { AssessmentLabel, ConfidenceLevel } from '@/types/api';

export function currency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('en-AU', {
    style: 'currency',
    currency: 'AUD',
    maximumFractionDigits: 0,
  }).format(value);
}

/** Compact form for headline figures: $915k, $2.45m. */
export function currencyCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value >= 1_000_000) {
    const millions = value / 1_000_000;
    return `$${millions.toFixed(millions >= 10 ? 1 : 2).replace(/\.?0+$/, '')}m`;
  }
  if (value >= 1_000) return `$${Math.round(value / 1_000)}k`;
  return `$${value}`;
}

export function distance(km: number | null | undefined): string {
  if (km === null || km === undefined) return '—';
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

export function shortDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function monthsAgo(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const months = (Date.now() - date.getTime()) / (1000 * 60 * 60 * 24 * 30.44);
  if (months < 1) return 'this month';
  if (months < 2) return '1 month ago';
  return `${Math.round(months)} months ago`;
}

export const ASSESSMENT_LABELS: Record<AssessmentLabel, string> = {
  underpriced: 'Below Evidence Range',
  fair: 'Reasonable',
  slightly_high: 'Slightly High',
  high: 'High',
  insufficient_evidence: 'Insufficient Evidence',
};

export const ASSESSMENT_EXPLANATIONS: Record<AssessmentLabel, string> = {
  underpriced:
    'The asking price sits below what comparable recent sales support. Often a sign of a guide price set to attract competition.',
  fair: 'The asking price sits within the range that comparable recent sales support.',
  slightly_high:
    'The asking price sits modestly above the comparable range. Some of the gap may be explained by features the comparables lack.',
  high: 'The asking price sits well above what the comparable evidence supports.',
  insufficient_evidence:
    'There was not enough comparable evidence to judge this asking price. This is a deliberate answer, not a failure.',
};

/**
 * Verdict colour, expressed only through semantic tokens so a verdict keeps its
 * meaning — and its contrast — in both themes. `dot`, `band` and `border` are
 * what the range chart paints with.
 */
export interface AssessmentStyle {
  text: string;
  bg: string;
  ring: string;
  dot: string;
  band: string;
  border: string;
}

export const ASSESSMENT_STYLES: Record<AssessmentLabel, AssessmentStyle> = {
  underpriced: {
    text: 'text-verdict-under',
    bg: 'bg-verdict-under/10',
    ring: 'ring-verdict-under/25',
    dot: 'bg-verdict-under',
    band: 'bg-verdict-under/25',
    border: 'border-verdict-under/40',
  },
  fair: {
    text: 'text-verdict-fair',
    bg: 'bg-verdict-fair/10',
    ring: 'ring-verdict-fair/25',
    dot: 'bg-verdict-fair',
    band: 'bg-verdict-fair/25',
    border: 'border-verdict-fair/40',
  },
  slightly_high: {
    text: 'text-verdict-slight',
    bg: 'bg-verdict-slight/10',
    ring: 'ring-verdict-slight/25',
    dot: 'bg-verdict-slight',
    band: 'bg-verdict-slight/25',
    border: 'border-verdict-slight/40',
  },
  high: {
    text: 'text-verdict-high',
    bg: 'bg-verdict-high/10',
    ring: 'ring-verdict-high/25',
    dot: 'bg-verdict-high',
    band: 'bg-verdict-high/25',
    border: 'border-verdict-high/40',
  },
  insufficient_evidence: {
    text: 'text-verdict-unknown',
    bg: 'bg-verdict-unknown/10',
    ring: 'ring-verdict-unknown/25',
    dot: 'bg-verdict-unknown',
    band: 'bg-verdict-unknown/25',
    border: 'border-verdict-unknown/40',
  },
};

export const CONFIDENCE_EXPLANATIONS: Record<ConfidenceLevel, string> = {
  high: 'Many close, recent, closely-matched comparable sales that agree with each other.',
  medium: 'A workable comparable set, with caveats listed under “What we could not assess”.',
  low: 'Few comparables, wide disagreement between them, or degraded matching. Treat the range as indicative only.',
};

export function titleCase(value: string): string {
  return value.replace(/[_-]/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}
