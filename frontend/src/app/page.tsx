import Link from "next/link";

import {
  capabilityCards,
  proofMetrics,
  workflowSteps,
  workspaceHighlights,
} from "@/lib/site-data";

export default function Home() {
  return (
    <main className="relative overflow-hidden bg-black">
      <section className="landing-page pb-36 md:pb-44">
        <div className="border-b border-white/[0.08] px-6 pt-5 md:px-10">
          <nav className="mx-auto flex max-w-6xl items-center justify-between gap-4 py-3.5">
            <div>
              <p className="text-2xl font-medium leading-none tracking-[-0.04em] text-white md:text-[2rem]">
                Helpmate AI
              </p>
            </div>

            <div className="hidden items-center gap-6 text-sm text-zinc-300 md:flex">
              <a href="#capabilities">Why it works</a>
              <a href="#workflow">Workflow</a>
              <a href="#proof">Proof</a>
            </div>

            <Link className="landing-nav-button" href="/app">
              Get started
            </Link>
          </nav>
        </div>

        <div className="mx-auto max-w-6xl px-6 md:px-10">
          <div className="mx-auto flex max-w-4xl flex-col items-center pb-10 pt-36 text-center md:pt-44">
            <h1 className="hero-title mt-6 max-w-5xl font-[family:var(--font-space-grotesk)] text-5xl font-semibold leading-[1.02] tracking-[-0.06em] md:text-7xl">
              Retrieval augmented document QA
            </h1>
            <p className="mt-6 max-w-4xl text-lg leading-8 text-zinc-300 md:text-xl">
              Upload a PDF or Word file, ask a question in plain language, and
              get a clear answer with the exact passages it came from.
            </p>
            <div className="mt-12 flex flex-wrap justify-center gap-4">
              <Link className="primary-button w-auto px-7 py-4" href="/app">
                Open workspace
              </Link>
              <a className="secondary-button w-auto px-7 py-4" href="#workflow">
                See how it works
              </a>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10 md:px-10" id="capabilities">
        <div className="section-shell p-6 md:p-8">
          <div className="grid gap-6 lg:grid-cols-[0.72fr_1.28fr] lg:items-start">
            <div className="dark-card p-6 md:p-7">
              <p className="eyebrow">Why it works</p>
              <h2 className="section-heading mt-4 text-3xl md:text-4xl">
                Built for documents people actually need to trust.
              </h2>
              <p className="mt-5 text-base leading-7 text-zinc-300">
                Helpmate is made for long files where accuracy matters:
                policies, theses, reports, and research papers. It keeps the
                answer clear and the source easy to inspect.
              </p>

              <div className="mt-7 grid gap-3">
                {workspaceHighlights.map((highlight) => (
                  <div
                    className="rounded-2xl border border-white/8 bg-white/[0.03] px-4 py-3 text-sm text-zinc-200"
                    key={highlight}
                  >
                    {highlight}
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              {capabilityCards.map((card) => (
                <article className="dark-card p-6 md:p-7" key={card.title}>
                  <h3 className="text-2xl font-semibold tracking-[-0.03em] text-white">
                    {card.title}
                  </h3>
                  <p className="mt-4 text-base leading-7 text-zinc-300">
                    {card.copy}
                  </p>
                </article>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10 md:px-10" id="workflow">
        <div className="section-shell p-6 md:p-8">
          <div className="mx-auto max-w-3xl text-center">
            <p className="eyebrow">Workflow</p>
            <h2 className="section-heading mt-4 text-3xl md:text-4xl">
              A simple flow for complex documents.
            </h2>
            <p className="mt-4 text-base leading-7 text-zinc-300 md:text-lg">
              Upload the file, ask the question, and verify the answer without
              digging through the whole document yourself.
            </p>
          </div>

          <div className="mt-8 grid gap-5 lg:grid-cols-[1.08fr_0.92fr]">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {workflowSteps.map((item) => (
                <article className="mini-card p-6" key={item.step}>
                  <div className="icon-ring">{item.step.slice(0, 1)}</div>
                  <h3 className="mt-6 text-2xl font-semibold tracking-[-0.03em] text-white">
                    {item.title}
                  </h3>
                  <p className="mt-4 text-base leading-7 text-zinc-300">
                    {item.body}
                  </p>
                </article>
              ))}
            </div>

            <article className="feature-showcase lg:min-h-full">
              <div className="feature-showcase-visual">
                <div className="feature-bars">
                  {workflowSteps.map((step, index) => (
                    <div className="feature-bar" key={step.step}>
                      <span>{step.step}</span>
                      <div
                        className="feature-bar-fill"
                        style={{ width: `${52 + index * 12}%` }}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-8">
                <p className="eyebrow">In the workspace</p>
                <h3 className="mt-4 font-[family:var(--font-space-grotesk)] text-3xl font-semibold tracking-[-0.04em] text-white">
                  Answers stay readable, and the evidence stays close.
                </h3>
                <p className="mt-4 max-w-xl text-base leading-7 text-zinc-300">
                  The interface is built to help people move from question to
                  answer to source without getting lost in a wall of text.
                </p>
                <div className="mt-7">
                  <Link className="secondary-button w-auto px-7 py-4" href="/app">
                    Start exploring now
                  </Link>
                </div>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10 md:px-10" id="proof">
        <div className="section-shell p-6 md:p-8">
          <div className="grid gap-6 lg:grid-cols-[0.72fr_1.28fr]">
            <div className="dark-card p-6 md:p-7">
              <p className="eyebrow">Proof</p>
              <h2 className="section-heading mt-4 text-3xl md:text-4xl">
                Trust should be visible, not implied.
              </h2>
              <p className="mt-5 text-base leading-7 text-zinc-300">
                Helpmate is benchmarked on policies, theses, and scientific
                papers so the product has a real quality story behind it, not
                just polished marketing language.
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {proofMetrics.map((metric) => (
                <article className="metric-card" key={metric.label}>
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                  <small>{metric.note}</small>
                </article>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-16 md:px-10">
        <div className="cta-shell">
          <div>
            <p className="eyebrow">Ready to try it?</p>
            <h2 className="section-heading mt-4 text-3xl md:text-5xl">
              Open a document and see how quickly the answers become usable.
            </h2>
            <p className="mt-5 max-w-2xl text-base leading-7 text-zinc-300 md:text-lg">
              Helpmate is built for the moment when you need an answer fast,
              but still want to know exactly where it came from.
            </p>
          </div>

          <div className="mt-8 flex flex-wrap gap-4">
            <Link className="primary-button w-auto px-7 py-4" href="/app">
              Open workspace
            </Link>
            <a className="secondary-button w-auto px-7 py-4" href="#capabilities">
              Why it works
            </a>
          </div>
        </div>
      </section>
    </main>
  );
}
