import { RecentAnalyses } from '@/components/RecentAnalyses';
import { SearchForm } from '@/components/SearchForm';
import { SystemStatus } from '@/components/SystemStatus';

export default function HomePage() {
  return (
    <div className="space-y-9">
      <section className="pt-4">
        <h1 className="max-w-2xl text-3xl font-semibold leading-tight tracking-tight text-ink-900 sm:text-4xl">
          Is this property really worth the asking price?
        </h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-ink-600">
          Paste a listing or an address. We find genuinely comparable recent sales nearby, show you
          exactly which ones and why, and tell you where the asking price sits against them — with
          the evidence, and the gaps, in plain sight.
        </p>
      </section>

      <SystemStatus />
      <SearchForm />

      <section className="grid gap-4 sm:grid-cols-3">
        <Point
          title="Evidence, not an estimate"
          body="No valuation model and no price prediction. The range comes from the sale prices of the comparables we show you, by arithmetic you could redo by hand."
        />
        <Point
          title="You can overrule it"
          body="Disagree with a comparable? Exclude it and re-run. The range, the verdict and the confidence are all recomputed — nothing is patched."
        />
        <Point
          title="It says what it does not know"
          body="Thin evidence produces “insufficient evidence”, not a confident guess. Everything it could not assess is listed alongside the answer."
        />
      </section>

      <RecentAnalyses />
    </div>
  );
}

function Point({ title, body }: { title: string; body: string }) {
  return (
    <div className="card card-pad">
      <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-600">{body}</p>
    </div>
  );
}
