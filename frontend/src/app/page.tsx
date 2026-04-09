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
          <div className="reveal reveal-1 mx-auto flex max-w-4xl flex-col items-center pb-10 pt-36 text-center md:pt-44">
            <h1 className="hero-title mt-6 max-w-5xl font-[family:var(--font-space-grotesk)] text-5xl font-semibold leading-[1.02] tracking-[-0.06em] md:text-7xl">
              Retrieval augmented
              <br />
              document QA
            </h1>
            <p className="mt-6 max-w-4xl text-lg leading-8 text-zinc-300 md:text-xl">
              Upload a PDF or Word file, ask a question in plain language, and get
              a clear answer with the exact passages it came from.
            </p>
            <div className="mt-12 flex flex-wrap justify-center gap-4">
              <Link className="primary-button w-auto px-7 py-4" href="/app">
                Open workspace
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-section mx-auto max-w-6xl px-6 py-14 md:px-10" id="capabilities">
        <div className="editorial-grid">
          <div className="reveal reveal-2 editorial-intro">
            <p className="eyebrow">Why it works</p>
            <h2 className="section-heading mt-4 text-3xl md:text-5xl">
              Built for documents people actually need to trust.
            </h2>
            <p className="mt-6 max-w-xl text-base leading-7 text-zinc-300 md:text-lg">
              Helpmate is made for long files where accuracy matters: policies,
              theses, reports, and research papers. It keeps the answer clear and
              the source easy to inspect.
            </p>

            <div className="mt-8 grid max-w-md gap-3">
              {workspaceHighlights.map((highlight, index) => (
                <div className={`soft-chip reveal reveal-${Math.min(index + 3, 6)}`} key={highlight}>
                  {highlight}
                </div>
              ))}
            </div>
          </div>

          <div className="reveal reveal-3 editorial-cards">
            {capabilityCards.map((card, index) => (
              <article
                className={`floating-card floating-card-${index + 1}`}
                key={card.title}
              >
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
      </section>

      <section className="landing-section mx-auto max-w-6xl px-6 py-14 md:px-10" id="workflow">
        <div className="reveal reveal-2 mx-auto max-w-3xl text-center">
          <p className="eyebrow">Workflow</p>
          <h2 className="section-heading mt-4 text-3xl md:text-5xl">
            A simple flow for complex documents.
          </h2>
          <p className="mt-4 text-base leading-7 text-zinc-300 md:text-lg">
            Upload the file, ask the question, and verify the answer without
            digging through the whole document yourself.
          </p>
        </div>

        <div className="workflow-grid mt-10">
          <div className="workflow-steps">
            {workflowSteps.map((item, index) => (
              <article className={`slim-step reveal reveal-${Math.min(index + 2, 6)}`} key={item.step}>
                <div className="slim-step-badge">{item.step.slice(0, 1)}</div>
                <h3 className="mt-6 text-2xl font-semibold tracking-[-0.03em] text-white">
                  {item.title}
                </h3>
                <p className="mt-4 text-base leading-7 text-zinc-300">{item.body}</p>
              </article>
            ))}
          </div>

          <article className="feature-spotlight reveal reveal-4">
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
                The interface is built to help people move from question to answer
                to source without getting lost in a wall of text.
              </p>
              <div className="mt-7">
                <Link className="secondary-button w-auto px-7 py-4" href="/app">
                  Start exploring now
                </Link>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section className="landing-section mx-auto max-w-6xl px-6 py-14 md:px-10" id="proof">
        <div className="proof-layout">
          <article className="proof-intro reveal reveal-2">
            <p className="eyebrow">Proof</p>
            <h2 className="section-heading mt-4 text-3xl md:text-5xl">
              Trust should be visible, not implied.
            </h2>
            <p className="mt-5 text-base leading-7 text-zinc-300 md:text-lg">
              Helpmate is benchmarked on policies, theses, and scientific papers so
              the product has a real quality story behind it, not just polished
              marketing language.
            </p>
          </article>

          <div className="proof-metrics">
            {proofMetrics.map((metric, index) => (
              <article className={`proof-card proof-card-${index + 1} reveal reveal-${Math.min(index + 3, 6)}`} key={metric.label}>
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.note}</small>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section mx-auto max-w-6xl px-6 pb-20 md:px-10">
        <div className="cta-ribbon reveal reveal-3">
          <div className="workflow-artboard-shell">
            <div className="workflow-artboard" aria-label="Helpmate workflow preview">
              <div className="workflow-artboard-glow" />
              <div className="workflow-artboard-vignette" />
              <div className="workflow-artboard-orb workflow-artboard-orb-a" />
              <div className="workflow-artboard-orb workflow-artboard-orb-b" />

              <div className="workflow-artboard-sidebar">
                <div className="workflow-artboard-brand">
                  <span className="workflow-artboard-brand-dot" />
                  <div>
                    <p>Helpmate</p>
                    <span>Document QA</span>
                  </div>
                </div>

                <div className="workflow-artboard-nav">
                  <span className="workflow-artboard-nav-item workflow-artboard-nav-item-active" />
                  <span className="workflow-artboard-nav-item" />
                  <span className="workflow-artboard-nav-item" />
                  <span className="workflow-artboard-nav-item" />
                </div>
              </div>

              <div className="workflow-artboard-main">
                <div className="workflow-artboard-topline">
                  <div className="workflow-artboard-pill-group">
                    <span className="workflow-artboard-pill workflow-artboard-pill-active" />
                    <span className="workflow-artboard-pill" />
                    <span className="workflow-artboard-pill" />
                  </div>
                  <div className="workflow-artboard-topline-right">
                    <div className="workflow-artboard-chip" />
                    <div className="workflow-artboard-chip workflow-artboard-chip-faint" />
                  </div>
                </div>

                <div className="workflow-artboard-canvas">
                  <div className="workflow-artboard-stage workflow-artboard-stage-upload">
                    <div className="workflow-artboard-stage-header">
                      <span className="workflow-artboard-dotline" />
                      <span className="workflow-artboard-stage-badge" />
                    </div>
                    <div className="workflow-artboard-line workflow-artboard-line-long" />
                    <div className="workflow-artboard-line workflow-artboard-line-mid" />
                    <div className="workflow-artboard-upload-row">
                      <div className="workflow-artboard-button-muted" />
                      <div className="workflow-artboard-button-primary" />
                    </div>
                  </div>

                  <div className="workflow-artboard-bridge">
                    <span className="workflow-artboard-bridge-node workflow-artboard-bridge-node-a" />
                    <span className="workflow-artboard-bridge-line" />
                    <span className="workflow-artboard-bridge-node workflow-artboard-bridge-node-b" />
                    <span className="workflow-artboard-bridge-line workflow-artboard-bridge-line-short" />
                    <span className="workflow-artboard-bridge-node workflow-artboard-bridge-node-c" />
                  </div>

                  <div className="workflow-artboard-stage-grid">
                    <div className="workflow-artboard-stage workflow-artboard-stage-index">
                      <span className="workflow-artboard-stage-badge" />
                      <div className="workflow-artboard-meter">
                        <div className="workflow-artboard-meter-fill workflow-artboard-meter-fill-a" />
                      </div>
                      <div className="workflow-artboard-meter">
                        <div className="workflow-artboard-meter-fill workflow-artboard-meter-fill-b" />
                      </div>
                    </div>

                    <div className="workflow-artboard-stage workflow-artboard-stage-answer">
                      <span className="workflow-artboard-stage-badge" />
                      <div className="workflow-artboard-answer-lines">
                        <span className="workflow-artboard-line workflow-artboard-line-card" />
                        <span className="workflow-artboard-line workflow-artboard-line-card-short" />
                        <span className="workflow-artboard-line workflow-artboard-line-card" />
                      </div>
                    </div>
                  </div>

                  <div className="workflow-artboard-bridge workflow-artboard-bridge-lower">
                    <span className="workflow-artboard-bridge-node workflow-artboard-bridge-node-b" />
                    <span className="workflow-artboard-bridge-line workflow-artboard-bridge-line-wide" />
                    <span className="workflow-artboard-bridge-node workflow-artboard-bridge-node-a" />
                  </div>

                  <div className="workflow-artboard-evidence">
                    <div className="workflow-artboard-evidence-card">
                      <span className="workflow-artboard-evidence-kicker" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence-short" />
                    </div>
                    <div className="workflow-artboard-evidence-card workflow-artboard-evidence-card-secondary">
                      <span className="workflow-artboard-evidence-kicker" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence-short" />
                    </div>
                    <div className="workflow-artboard-evidence-card workflow-artboard-evidence-card-tertiary">
                      <span className="workflow-artboard-evidence-kicker" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence" />
                      <div className="workflow-artboard-line workflow-artboard-line-evidence-short" />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
