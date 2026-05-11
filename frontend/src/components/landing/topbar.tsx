"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// Topbar — chrome.md §"Topbar". Two render shapes:
//
//  1. Default (landing root + any future primary surface):
//     brand left · nav center · primary CTA right. Mobile collapses
//     to brand + burger; burger dropdown contains the nav + a copy
//     of the CTA. ESC / tap-outside / resize-to-desktop all close
//     the menu; body scroll is locked while open.
//
//  2. Secondary (any page whose pathname ends in /privacy-policy):
//     brand left · "← Back" CTA right. No nav, no burger. The
//     Back link is wired to "/" — the brand mark already does
//     the same thing, so the explicit Back affordance is the
//     duplicate the user wanted for primary navigation back home.

const WORKSPACE_URL = "https://app.helpmateai.xyz";

const NAV_LINKS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#validation", label: "Evaluation" },
] as const;

export function LandingTopbar() {
  const pathname = usePathname();
  // endsWith() handles both the dev path (/landing/privacy-policy) and
  // the prod path (/privacy-policy after the helpmateai.xyz host rewrite).
  const isSecondary = pathname?.endsWith("/privacy-policy") ?? false;

  const [menuOpen, setMenuOpen] = useState(false);

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

  if (isSecondary) {
    return (
      <header className="l-topbar">
        <div className="l-topbar-inner">
          <div className="l-brand">
            <img
              alt=""
              aria-hidden
              className="l-brand-mark"
              height={28}
              src="/brand/helpmate-icon.svg"
              width={28}
            />
            <div className="l-wordmark">Helpmate AI</div>
          </div>
          <Link href="/" className="l-cta l-cta-back">
            <span aria-hidden>←</span>
            Back
          </Link>
        </div>
      </header>
    );
  }

  const close = () => setMenuOpen(false);

  return (
    <>
      <header className="l-topbar">
        <div className="l-topbar-inner">
          <div className="l-brand">
            <img
              alt=""
              aria-hidden
              className="l-brand-mark"
              height={28}
              src="/brand/helpmate-icon.svg"
              width={28}
            />
            <div className="l-wordmark">Helpmate AI</div>
          </div>
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
