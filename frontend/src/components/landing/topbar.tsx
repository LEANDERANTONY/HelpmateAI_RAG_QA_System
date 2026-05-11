"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

// Topbar — chrome.md §"Topbar". On desktop (>900px): brand left,
// nav center, primary CTA right. On mobile (≤900px): brand left,
// burger right; tapping the burger opens a near-opaque fixed
// dropdown with the same links + a copy of the CTA.
//
// State-managed in this component (rather than CSS-only :target
// or a hidden checkbox) so we can:
//   • lock body scroll while the menu is open
//   • close on ESC
//   • close on backdrop tap
//   • close automatically when the viewport widens above 900px
//   • close after a link click before the smooth-scroll fires

const WORKSPACE_URL = "https://app.helpmateai.xyz";

const NAV_LINKS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#validation", label: "Evaluation" },
] as const;

export function LandingTopbar() {
  const [menuOpen, setMenuOpen] = useState(false);

  // Body scroll lock + ESC dismiss + auto-close on resize-to-desktop.
  useEffect(() => {
    if (!menuOpen) return;

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    const onResize = () => {
      if (window.matchMedia("(min-width: 901px)").matches) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);

    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [menuOpen]);

  const close = () => setMenuOpen(false);

  return (
    <>
      <header className="l-topbar">
        <div className="l-topbar-inner">
          <Link href="/" className="l-brand" aria-label="Helpmate AI home">
            <img
              alt=""
              aria-hidden
              className="l-brand-mark"
              height={28}
              src="/brand/helpmate-icon.svg"
              width={28}
            />
            <div className="l-wordmark">Helpmate AI</div>
          </Link>
          <nav className="l-nav" aria-label="Primary">
            {NAV_LINKS.map((link) => (
              <a key={link.href} href={link.href}>
                {link.label}
              </a>
            ))}
          </nav>
          <a className="l-cta" href={WORKSPACE_URL}>
            Open workspace
            <span aria-hidden>→</span>
          </a>
          <button
            type="button"
            className="l-burger"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            aria-controls="l-mobile-menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className="bar" aria-hidden />
            <span className="bar" aria-hidden />
            <span className="bar" aria-hidden />
          </button>
        </div>
      </header>

      {menuOpen && (
        <div
          className="l-mobile-menu"
          id="l-mobile-menu"
          role="dialog"
          aria-modal="true"
          aria-label="Site menu"
          onClick={(e) => {
            // backdrop tap dismiss — only when the click lands on
            // the panel itself, not inside .l-mobile-menu-inner
            if (e.target === e.currentTarget) close();
          }}
        >
          <div className="l-mobile-menu-inner">
            <nav aria-label="Mobile primary">
              {NAV_LINKS.map((link) => (
                <a key={link.href} href={link.href} onClick={close}>
                  {link.label}
                </a>
              ))}
              <a
                href="https://helpmateai.xyz/privacy-policy"
                onClick={close}
              >
                Privacy Policy
              </a>
            </nav>
            <a className="l-cta" href={WORKSPACE_URL} onClick={close}>
              Open workspace
              <span aria-hidden>→</span>
            </a>
          </div>
        </div>
      )}
    </>
  );
}
