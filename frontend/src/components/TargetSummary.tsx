import type { PropertyRecord } from '@/types/api';
import { titleCase } from '@/lib/format';

/** The property under analysis, and what is actually known about it. */
export function TargetSummary({
  property,
  description,
}: {
  property: PropertyRecord;
  description: string | null;
}) {
  const attributes: Array<[string, string]> = [
    ['Type', titleCase(property.property_type)],
    ['Bedrooms', property.bedrooms?.toString() ?? 'not recorded'],
    ['Bathrooms', property.bathrooms?.toString() ?? 'not recorded'],
    ['Car spaces', property.carspaces?.toString() ?? 'not recorded'],
    ['Internal area', property.floor_area_sqm ? `${property.floor_area_sqm} m²` : 'not recorded'],
    ['Land area', property.land_area_sqm ? `${property.land_area_sqm} m²` : 'not recorded'],
  ];

  return (
    <section className="card overflow-hidden">
      <div className="card-pad">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h1 className="text-xl font-bold tracking-tight text-ink-950 sm:text-2xl">
              {property.address}
            </h1>
            <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-500">
              <span aria-hidden className="text-ink-400">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <path d="M13 6.8c0 3.4-5 8.2-5 8.2S3 10.2 3 6.8a5 5 0 0 1 10 0Z" />
                  <circle cx="8" cy="6.7" r="1.7" />
                </svg>
              </span>
              {property.suburb} {property.state} {property.postcode}
            </p>
          </div>
          {property.is_demo_data && (
            <span className="chip bg-warn-bg text-warn-fg ring-1 ring-warn-border">
              demonstration data
            </span>
          )}
        </div>
      </div>

      {/* gap-px over an ink background rules the spec grid however it wraps. */}
      <dl className="grid grid-cols-2 gap-px border-t border-ink-200 bg-ink-200 sm:grid-cols-3 lg:grid-cols-6">
        {attributes.map(([term, value]) => {
          const unknown = value === 'not recorded';
          return (
            <div key={term} className="bg-surface px-5 py-3">
              <dt className="label">{term}</dt>
              <dd
                className={`mt-1 text-sm tabular-nums ${
                  unknown ? 'italic text-ink-400' : 'font-semibold text-ink-900'
                }`}
              >
                {value}
              </dd>
            </div>
          );
        })}
      </dl>

      {description && (
        <details className="group border-t border-ink-200">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-xs font-semibold text-ink-600 transition-colors hover:bg-surface-sunken hover:text-ink-900 sm:px-6">
            <span
              aria-hidden
              className="text-ink-400 transition-transform duration-200 group-open:rotate-90"
            >
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M6 3.5 10.5 8 6 12.5" />
              </svg>
            </span>
            Listing description
          </summary>
          <p className="px-5 pb-4 text-sm leading-relaxed text-ink-700 sm:px-6">{description}</p>
        </details>
      )}
    </section>
  );
}
