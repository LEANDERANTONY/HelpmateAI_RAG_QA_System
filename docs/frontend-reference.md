# Frontend Reference

This document captures the current design reference for the upcoming custom frontend phase.

## Primary Reference

- published Framer template:
  - [Portfolite Template Preview](https://coral-phoenix-737374.framer.app/)

This published site is already enough to use as a structural and aesthetic reference for the Helpmate frontend rebuild.

## What We Can Reuse From This Reference

The Framer reference has a stronger product feel than the earlier prototype shell because it uses:

- a clearer landing-page narrative
- stronger section hierarchy
- more intentional spacing
- more premium CTA placement
- richer visual rhythm across long scrolling sections
- a more portfolio-grade presentation than a utility-first app shell

## Structural Patterns To Borrow

From the published site structure, these patterns are especially relevant:

- anchored top navigation
- strong hero with a primary CTA and secondary CTA
- long-form single-page storytelling
- section-based rhythm:
  - hero
  - project/work showcase
  - about section
  - process section
  - services section
  - testimonials/social proof
  - FAQ
  - final CTA footer block

## How This Maps To Helpmate

We should not copy the portfolio content literally. We should map the structure into a product flow.

Recommended Helpmate mapping:

- hero
  - product positioning
  - upload / try sample document CTA
- sample documents / benchmark proof
  - use the current `static/` sample documents
  - show benchmark credibility and document families
- how it works
  - ingest
  - index
  - retrieve
  - answer
  - cite
- capabilities
  - hybrid retrieval
  - structure-aware retrieval
  - section-first routing
  - grounded abstention
  - benchmarks
- product workspace preview
  - upload area
  - answer panel
  - evidence panel
  - benchmark/debug panel
- FAQ
  - supported documents
  - local-first indexing
  - difference from generic file chat
- final CTA
  - upload a document
  - explore sample documents

## What To Export Or Share From Framer

For building the real frontend, the most useful handoff is not raw generated site code. The most useful handoff is:

1. published site URL
2. desktop screenshots of the main sections
3. mobile screenshots of the same sections
4. exported assets:
   - logos
   - illustrations
   - icons
   - background textures or images
5. font names
6. any custom brand colors you want preserved
7. any sections/components you especially want reused

This is better than relying on brittle exported Framer output as the production app codebase.

## About Framer Export

Framer’s current documentation is somewhat mixed:

- one help article says Framer does not offer HTML exporting for self-hosting because of its dynamic backend services:
  - [Can I export my website to HTML and self-host it?](https://www.framer.com/help/articles/can-i-export-my-website-to-html-and-self-host-it/)
- another newer portability article says published output can be downloaded as standard HTML, CSS, JavaScript, and assets:
  - [Porting your data from Framer](https://www.framer.com/help/articles/porting-your-data-from-framer/)

Because of that ambiguity, we should not depend on exported Framer code as the foundation of the app frontend.

## Recommended Handoff Strategy

Use Framer as the design source of truth, not as the production code source.

Best practical approach:

- keep the published Framer page as visual reference
- add screenshots and asset exports for higher-fidelity rebuilding
- recreate the UI cleanly in `Next.js`
- keep the current Python retrieval core intact behind `FastAPI`

## Next Frontend Build Direction

Recommended target:

- `frontend/` with `Next.js`
- `backend/` with `FastAPI`
- current Python retrieval modules reused behind API endpoints

This keeps the premium frontend goal aligned with the backend system we already built.
