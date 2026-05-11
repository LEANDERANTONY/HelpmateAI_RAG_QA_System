"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLandingHref } from "@/components/landing/use-landing-href";

export function LandingFooter() {
  const pathname = usePathname();
  // On the privacy policy page itself, the "Privacy Policy" Resources
  // link would be a self-link — swap it for a Home link back to the
  // landing root.
  const isSecondary = pathname?.endsWith("/privacy-policy") ?? false;
  const landingHref = useLandingHref();

  return (
    <footer className="l-foot">
      <div className="l-foot-inner">
        <div className="l-foot-brand">
          <div className="word">Helpmate AI</div>
          <div className="tag">
            Document answers you can verify. Built for the questions where the
            answer matters more than the wait.
          </div>
          <div className="by">Built by Leander Antony A</div>
        </div>
        <div className="l-foot-col">
          <div className="head">Resources</div>
          {isSecondary ? (
            <Link href={landingHref}>Home</Link>
          ) : (
            <a href="https://helpmateai.xyz/privacy-policy">Privacy Policy</a>
          )}
        </div>
        <div className="l-foot-col">
          <div className="head">Socials</div>
          <a
            href="https://github.com/LEANDERANTONY"
            target="_blank"
            rel="noreferrer"
          >
            Github
          </a>
          <a
            href="https://www.linkedin.com/in/leander-antony-a-176319147"
            target="_blank"
            rel="noreferrer"
          >
            LinkedIn
          </a>
        </div>
      </div>
    </footer>
  );
}
