import { currency, currencyCompact } from '@/lib/format';

/**
 * The answer, as a picture.
 *
 * Four numbers — asking price, range low, range high, median — carry the whole
 * verdict, and read as four numbers they force the reader to do the comparison
 * in their head. On one axis the comparison is already done: the asking marker
 * is inside the band or it is not, and the distance is to scale.
 *
 * Every sale that built the range is plotted on the same axis, so the band is
 * visibly the middle of a real distribution rather than an assertion. This is a
 * bullet chart: a measure (asking) against a qualitative range (the evidence),
 * which is exactly the shape of this question.
 *
 * Purely presentational — it derives its geometry from the numbers it is given
 * and holds no state.
 */

type Sale = { id: string; price: number; included: boolean };

const CLAMP = (value: number) => Math.min(100, Math.max(0, value));

export function PriceRangeChart({
  askingPrice,
  low,
  high,
  median,
  sales,
  tone,
  verdictLabel,
}: {
  askingPrice: number | null;
  low: number | null;
  high: number | null;
  median: number | null;
  sales: Sale[];
  tone: { text: string; dot: string; band: string; border: string };
  verdictLabel: string;
}) {
  const included = sales.filter((sale) => sale.included);
  const points = [askingPrice, low, high, median, ...included.map((sale) => sale.price)].filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  );

  // Nothing to plot against: the panel above already says "insufficient
  // evidence", so an empty axis would only add noise.
  if (low === null || high === null || points.length < 2) return null;

  const rawMin = Math.min(...points);
  const rawMax = Math.max(...points);
  const span = rawMax - rawMin;
  // A degenerate span (every sale at the same price) would divide by zero.
  const pad = span > 0 ? span * 0.14 : Math.max(rawMax * 0.05, 1);
  const domainMin = rawMin - pad;
  const domainMax = rawMax + pad;
  const width = domainMax - domainMin || 1;
  const pct = (value: number) => CLAMP(((value - domainMin) / width) * 100);

  const bandStart = pct(low);
  const bandEnd = pct(high);
  const bandWidth = Math.max(bandEnd - bandStart, 0.6);
  const roomyBand = bandWidth > 24;

  const askingPct = askingPrice === null ? null : pct(askingPrice);
  const outside =
    askingPrice !== null && (askingPrice > high || askingPrice < low)
      ? askingPrice > high
        ? askingPrice - high
        : low - askingPrice
      : null;

  const summary =
    askingPrice === null
      ? `Comparable sales support a range from ${currency(low)} to ${currency(high)}.`
      : `The asking price of ${currency(askingPrice)} sits ${
          outside === null
            ? 'inside'
            : `${currency(outside)} ${askingPrice > high ? 'above the top of' : 'below the bottom of'}`
        } the evidence range of ${currency(low)} to ${currency(high)}, built from ${
          included.length
        } comparable ${included.length === 1 ? 'sale' : 'sales'}. Assessment: ${verdictLabel}.`;

  return (
    <figure className="mt-1">
      {/* The chart is decorative to a screen reader; this sentence is the data. */}
      <p className="sr-only">{summary}</p>

      <div aria-hidden className="px-1 pt-8 sm:pt-9">
        {/* Asking-price marker, above the axis so it reads as the thing being
            measured rather than another data point on it. */}
        <div className="relative h-9">
          {askingPct !== null && (
            <div
              className="absolute top-0 flex flex-col items-center"
              style={{ left: `${askingPct}%`, transform: 'translateX(-50%)' }}
            >
              <div
                className={`animate-marker-in whitespace-nowrap rounded-md border ${tone.border} bg-surface
                            px-2 py-1 text-[13px] font-semibold tabular-nums leading-none shadow-pop ${tone.text}`}
              >
                {currency(askingPrice)}
              </div>
              <div className={`mt-0.5 h-3 w-px ${tone.dot} opacity-60`} />
            </div>
          )}
        </div>

        {/* The axis. The track is inset — a groove rather than a bar — so the
            evidence band reads as sitting in it, which is the whole metaphor:
            the range is the channel, the asking price is where it lands. */}
        <div
          className="relative h-3 rounded-full bg-ink-200/70"
          style={{ boxShadow: 'inset 0 1px 2px rgb(0 0 0 / 0.12)' }}
        >
          <div
            className={`absolute inset-y-0 origin-left animate-band-in rounded-full ${tone.band}`}
            style={{ left: `${bandStart}%`, width: `${bandWidth}%` }}
          />
          {median !== null && (
            <div
              title="median"
              className="absolute -inset-y-1 w-px rounded-full bg-ink-600/45"
              style={{ left: `${pct(median)}%`, transform: 'translateX(-50%)' }}
            />
          )}
          {askingPct !== null && (
            /* `tone.text` is carried here purely so `currentColor` resolves to
               the verdict hue — the fill itself still comes from `tone.dot`.
               Without it the glow would pick up inherited body ink. */
            <div
              className={`absolute -inset-y-[6px] w-[3px] rounded-full ${tone.dot} ${tone.text} ring-2 ring-surface`}
              style={{
                left: `${askingPct}%`,
                transform: 'translateX(-50%)',
                boxShadow: '0 0 12px 1px currentColor',
              }}
            />
          )}
        </div>

        {/* Every sale that built the range, to scale. */}
        <div className="relative mt-2 h-3">
          {included.map((sale) => (
            <span
              key={sale.id}
              title={currency(sale.price)}
              className="absolute top-0 h-1.5 w-1.5 rounded-full bg-ink-400"
              style={{ left: `${pct(sale.price)}%`, transform: 'translateX(-50%)' }}
            />
          ))}
        </div>

        {/* Endpoint labels. A narrow band cannot carry two of them without them
            colliding, so it gets one centred label instead. */}
        <div className="relative h-8">
          {roomyBand ? (
            <>
              <Tick left={bandStart} value={low} caption="range low" />
              <Tick left={bandEnd} value={high} caption="range high" />
            </>
          ) : (
            <Tick
              left={(bandStart + bandEnd) / 2}
              value={null}
              caption="evidence range"
              text={`${currencyCompact(low)} – ${currencyCompact(high)}`}
            />
          )}
        </div>
      </div>

      <figcaption className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 text-[11px] text-ink-500">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className={`h-2 w-3 rounded-sm ${tone.band}`} />
          middle 50% of comparable sales
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-ink-400" />
          {included.length} comparable {included.length === 1 ? 'sale' : 'sales'}
        </span>
        {median !== null && (
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="h-3 w-px bg-ink-600/45" />
            median
          </span>
        )}
        {outside !== null && askingPrice !== null && (
          <span className={`font-medium ${tone.text}`}>
            {currency(outside)} {askingPrice > high ? 'above' : 'below'} the range
          </span>
        )}
      </figcaption>
    </figure>
  );
}

function Tick({
  left,
  value,
  caption,
  text,
}: {
  left: number;
  value: number | null;
  caption: string;
  text?: string;
}) {
  return (
    <span
      className="absolute top-0 flex flex-col items-center gap-0.5"
      style={{ left: `${left}%`, transform: 'translateX(-50%)' }}
    >
      <span className="text-xs font-semibold tabular-nums text-ink-700">
        {text ?? currencyCompact(value)}
      </span>
      <span className="whitespace-nowrap text-[10px] uppercase tracking-wide text-ink-400">
        {caption}
      </span>
    </span>
  );
}
