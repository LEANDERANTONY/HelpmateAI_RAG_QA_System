"use client";

import { useEffect } from "react";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function ErrorPage({ error, reset }: ErrorPageProps) {
  useEffect(() => {
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
