import { RecentAnalyses } from '@/components/RecentAnalyses';
import { SearchForm } from '@/components/SearchForm';
import { SystemStatus } from '@/components/SystemStatus';

export default function HomePage() {
  return (
    <div className="space-y-10 sm:space-y-12">
      <section className="stagger pt-4 sm:pt-10">
        <p className="label mb-3.5 flex items-center gap-2.5">
          <span aria-hidden className="h-px w-7 bg-gradient-to-r from-accent-500 to-ink-300" />
          Comparable-sales analysis
        </p>
        {/*
         * The question is the product, so it is set as the question — the
         * operative clause carries the accent while the rest stays ink, which
         * puts the emphasis where a person would put it saying it aloud.
         */}
        <h1 className="max-w-3xl text-display font-bold text-ink-950">
          Is this property{' '}
          <span className="bg-gradient-to-br from-accent-600 to-accent-400 bg-clip-text text-transparent">
            really worth
          </span>{' '}
          the asking price?
        </h1>
        <p className="mt-5 max-w-2xl text-lead text-ink-600">
          Paste a listing or an address. We find genuinely comparable recent sales nearby, show you
          exactly which ones and why, and tell you where the asking price sits against them — with
          the evidence, and the gaps, in plain sight.
        </p>
      </section>

      <div className="space-y-4">
        <SystemStatus />
        <SearchForm />
      </div>

      <section aria-label="What this tool does" className="stagger grid gap-4 sm:grid-cols-3">
        <Point
          icon={<ScaleIcon />}
          title="Evidence, not an estimate"
          body="No valuation model and no price prediction. The range comes from the sale prices of the comparables we show you, by arithmetic you could redo by hand."
        />
        <Point
          icon={<HandIcon />}
          title="You can overrule it"
          body="Disagree with a comparable? Exclude it and re-run. The range, the verdict and the confidence are all recomputed — nothing is patched."
        />
        <Point
          icon={<QuestionIcon />}
          title="It says what it does not know"
          body="Thin evidence produces “insufficient evidence”, not a confident guess. Everything it could not assess is listed alongside the answer."
        />
      </section>

      <RecentAnalyses />
    </div>
  );
}

function Point({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="card card-interactive card-pad">
      <span
        aria-hidden
        className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-100
                   text-accent-700 ring-1 ring-inset ring-accent-200/60"
      >
        {icon}
      </span>
      <h2 className="mt-3.5 text-sm font-semibold text-ink-900">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-600">{body}</p>
    </div>
  );
}

/* One icon family: 20px box, 1.6 stroke, round caps — drawn inline so the
   product carries no icon dependency and no two icons can drift apart. */
const ICON = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  className: 'h-[18px] w-[18px]',
};

function ScaleIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <path d="M10 3.5v13M5 6.5h10M4 16.5h12" />
      <path d="M5 6.5 2.75 11.5h4.5L5 6.5ZM15 6.5l-2.25 5h4.5L15 6.5Z" />
    </svg>
  );
}

function HandIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <path d="M7.5 10.5V4.75a1.25 1.25 0 0 1 2.5 0V9m0 0V3.75a1.25 1.25 0 0 1 2.5 0V9m0 0V5.75a1.25 1.25 0 0 1 2.5 0v6.4a4.85 4.85 0 0 1-4.85 4.85h-.9a4.25 4.25 0 0 1-3.5-1.85L3.6 12.4a1.2 1.2 0 0 1 1.85-1.5l2.05 1.85" />
    </svg>
  );
}

function QuestionIcon() {
  return (
    <svg {...ICON} aria-hidden>
      <circle cx="10" cy="10" r="7.25" />
      <path d="M8.1 8a1.95 1.95 0 1 1 2.6 1.85c-.5.2-.7.6-.7 1.1v.4" />
      <path d="M10 14.05h.008" strokeWidth="2" />
    </svg>
  );
}
