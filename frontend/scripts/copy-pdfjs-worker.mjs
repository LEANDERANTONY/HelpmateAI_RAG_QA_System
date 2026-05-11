// Copies pdfjs-dist's worker bundle to public/ so Next.js can serve it
// as a static asset. The worker is fetched by PDF.js at runtime via
// GlobalWorkerOptions.workerSrc — set to "/pdf.worker.min.mjs" in
// lib/pdfjs-loader.ts.
//
// Why a copy step instead of `new Worker(new URL(...))`?
// The ESM new-Worker pattern requires bundler cooperation (Webpack 5 /
// Turbopack / Vite all handle it differently, and the Next 16 Turbopack
// support for worker modules is still inconsistent). Copying to public/
// is the boring, bundler-agnostic path that ships across Vercel and the
// VPS without surprise.
//
// Runs at postinstall so the worker version always matches the installed
// pdfjs-dist version. Re-running is idempotent.

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const SOURCE = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
// resolve relative to this script's directory so it works regardless of
// where npm runs the postinstall from. fileURLToPath handles the URL
// encoding (e.g. spaces in Windows user-profile paths) that a naive
// new URL().pathname would leave as %20.
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEST = resolve(SCRIPT_DIR, "..", "public", "pdf.worker.min.mjs");

mkdirSync(dirname(DEST), { recursive: true });
copyFileSync(SOURCE, DEST);

console.log(`[pdfjs] worker copied: ${SOURCE} -> ${DEST}`);
