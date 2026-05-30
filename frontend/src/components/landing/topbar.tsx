"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useLandingHref } from "@/components/landing/use-landing-href";

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

  const landingHref = useLandingHref();

  const [menuOpen, setMenuOpen] = useState(false);
  // Focus-trap refs (M21): hand focus back to the burger when the dialog
  // closes, and scope the Tab trap to the dialog subtree.
  const burgerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // The mobile menu is role="dialog" aria-modal="true", so it must own
    // focus while open (WCAG 2.4.3 + the aria-modal contract): move focus
    // in on open, keep Tab inside, and hand it back to the burger on close.
    // Without this, Tab walked straight out to the page controls sitting
    // behind the overlay.
    const dialog = dialogRef.current;
    // Capture the trigger now; it's stable (always mounted in the header)
    // so reading it in cleanup is safe, but copying satisfies the lint rule
    // about refs changing before cleanup runs.
    const burger = burgerRef.current;
    const getFocusable = (): HTMLElement[] =>
      dialog
        ? Array.from(
            dialog.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ),
          )
        : [];

    // Move focus to the first menu item (fall back to the dialog itself,
    // which carries tabIndex={-1}).
    (getFocusable()[0] ?? dialog)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const items = getFocusable();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // Wrap at both ends, and pull focus back in if it has somehow escaped
      // the dialog (e.g. it started on the burger, which lives outside it).
      if (e.shiftKey) {
        if (active === first || !dialog?.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !dialog?.contains(active)) {
        e.preventDefault();
        first.focus();
      }
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
      // Return focus to the trigger so a keyboard user lands back where
      // they opened the menu (no-op on desktop, where the burger is hidden).
      burger?.focus();
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
          <Link href={landingHref} className="l-cta l-cta-back">
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
            ref={burgerRef}
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
          ref={dialogRef}
          className="l-mobile-menu"
          id="l-mobile-menu"
          role="dialog"
          aria-modal="true"
          aria-label="Site menu"
          tabIndex={-1}
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
