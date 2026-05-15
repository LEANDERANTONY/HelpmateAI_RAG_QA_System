"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function ErrorPage({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    // Belt-and-suspenders: the App Router's onRequestError hook in
    // ``instrumentation.ts`` already forwards uncaught Server
    // Component errors to Sentry, but client-side rendering errors
    // surface here and need explicit capture to attach the React
    // component stack from the error boundary.
    Sentry.captureException(error);
    // eslint-disable-next-line no-console
    console.error(error);
  }, [error]);

  return (
    <main className="workspace-page flex-1">
      <div className="h-shell">
        <div className="h-error-fallback">
          <div className="h-error-card">
            <p className="h-eyebrow">Unexpected error</p>
            <h1 className="h-error-fallback-title">The workspace hit a snag.</h1>
            <p className="h-error-fallback-body">
              Reload to keep going. Your last document and answers should restore.
            </p>
            <div className="h-error-fallback-actions">
              <button
                className="h-btn h-btn-primary"
                onClick={() => reset()}
                type="button"
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
