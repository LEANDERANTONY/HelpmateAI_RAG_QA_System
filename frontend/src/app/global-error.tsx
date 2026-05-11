"use client";

import { useEffect } from "react";

type GlobalErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function GlobalErrorPage({ error, reset }: GlobalErrorPageProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          alignItems: "center",
          background: "#04070f",
          color: "#f5f8ff",
          display: "flex",
          fontFamily:
            "var(--font-dm-sans), system-ui, -apple-system, sans-serif",
          justifyContent: "center",
          margin: 0,
          minHeight: "100vh",
          padding: "32px",
        }}
      >
        <div
          style={{
            background: "rgba(10, 14, 22, 0.72)",
            border: "1px solid rgba(160, 178, 218, 0.20)",
            borderRadius: 14,
            maxWidth: 480,
            padding: "28px 32px",
            width: "100%",
          }}
        >
          <p
            style={{
              color: "#8a93a8",
              fontFamily:
                "var(--font-geist-mono), ui-monospace, monospace",
              fontSize: 11,
              fontWeight: 500,
              letterSpacing: "0.08em",
              margin: 0,
              textTransform: "uppercase",
            }}
          >
            Critical error
          </p>
          <h1
            style={{
              color: "#f5f8ff",
              fontFamily:
                "var(--font-space-grotesk), system-ui, sans-serif",
              fontSize: 22,
              fontWeight: 600,
              margin: "10px 0 8px",
            }}
          >
            We couldn&apos;t load the app.
          </h1>
          <p
            style={{
              color: "#c7cfdf",
              fontSize: 14,
              lineHeight: 1.5,
              margin: "0 0 20px",
            }}
          >
            Reload to try again. If it keeps happening, refresh the browser.
          </p>
          <button
            onClick={() => reset()}
            style={{
              background: "#7fe0b0",
              border: "none",
              borderRadius: 999,
              color: "#04241a",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: 13,
              fontWeight: 600,
              minHeight: 36,
              padding: "8px 18px",
            }}
            type="button"
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
