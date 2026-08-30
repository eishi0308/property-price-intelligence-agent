import Link from 'next/link';

const STEPS = [
  {
    title: 'Resolve the property',
    body: 'A pasted realestate.com.au or Domain URL is parsed locally to recover the address it refers to — the sites themselves are never scraped. That address is then looked up through a licensed property-data provider.',
  },
  {
    title: 'Rule out what is not comparable',
    body: 'A SQL query narrows recent nearby sales by property type, distance, recency, bedrooms and size. This part is deliberately deterministic: a 4-bedroom house is never a comparable for a 2-bedroom apartment, however similarly the two listings are written.',
  },
  {
    title: 'Rank what is left, three ways',
    body: 'Structural closeness catches the measurable. PostgreSQL full-text search catches the literal — "north-facing", "lock-up garage". Vector similarity catches the paraphrased — "bathed in morning sun" meaning the same thing. Their rankings are fused, because each one is blind to what the others see.',
  },
  {
    title: 'Grade the shortlist',
    body: 'The strongest candidates are graded on comparability the way a valuer would weigh them — type, location, recency, size, then condition and features. Only the best five to ten become evidence.',
  },
  {
    title: 'Compute the range, then explain it',
    body: 'The evidence range is the interquartile range of those sale prices: arithmetic, not a model. Only after the numbers are fixed is a written explanation produced, and it may only cite the evidence actually retrieved.',
  },
  {
    title: 'Check it before you see it',
    body: 'Guardrails strip any claim of guaranteed value, future price, investment return or financial advice; verify every cited source exists; re-read comparable prices from the database rather than trusting the narrative; and cap confidence at what the evidence supports.',
  },
];

export default function HowItWorksPage() {
  return (
    <div className="max-w-3xl space-y-8 pt-4">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-ink-900">How it works</h1>
        <p className="mt-3 text-base leading-relaxed text-ink-600">
          This tool answers one question: does the asking price look reasonable next to genuinely
          similar recent sales? Everything below exists to make that answer checkable.
        </p>
      </header>

      <ol className="space-y-4">
        {STEPS.map((step, index) => (
          <li key={step.title} className="card card-pad flex gap-4">
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-100 text-sm font-semibold text-accent-800"
            >
              {index + 1}
            </span>
            <div>
              <h2 className="text-sm font-semibold text-ink-900">{step.title}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-600">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>

      <section className="card card-pad">
        <h2 className="text-sm font-semibold text-ink-900">What this is not</h2>
        <ul className="mt-2.5 space-y-2 text-sm leading-relaxed text-ink-600">
          {[
            'Not a valuation. No licensed valuer has inspected anything, and no valuation model is used.',
            'Not a price prediction. Nothing here forecasts what a property will sell for or be worth later.',
            'Not financial or legal advice. It will not tell you what to offer or whether to buy.',
            'Not a substitute for a building inspection, a strata report, or a contract review.',
          ].map((item) => (
            <li key={item} className="flex gap-2.5">
              <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-400" />
              {item}
            </li>
          ))}
        </ul>
      </section>

      <Link href="/" className="btn-primary">
        Analyse a property
      </Link>
    </div>
  );
}
