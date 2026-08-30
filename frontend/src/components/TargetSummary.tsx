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
    <section className="card card-pad">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">{property.address}</h1>
          <p className="mt-0.5 text-sm text-ink-500">
            {property.suburb} {property.state} {property.postcode}
          </p>
        </div>
        {property.is_demo_data && (
          <span className="chip bg-amber-100 text-amber-800">demonstration data</span>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-2.5 sm:grid-cols-3 lg:grid-cols-6">
        {attributes.map(([term, value]) => (
          <div key={term}>
            <dt className="label">{term}</dt>
            <dd
              className={`mt-0.5 text-sm ${
                value === 'not recorded' ? 'italic text-ink-400' : 'font-medium text-ink-900'
              }`}
            >
              {value}
            </dd>
          </div>
        ))}
      </dl>

      {description && (
        <details className="mt-4 border-t border-ink-200 pt-3.5">
          <summary className="cursor-pointer text-xs font-semibold text-ink-600 hover:text-ink-900">
            Listing description
          </summary>
          <p className="mt-2 text-sm leading-relaxed text-ink-700">{description}</p>
        </details>
      )}
    </section>
  );
}
