// Placeholder page — added in Phase 3 so the footer's
// "/privacy-policy" link resolves to a real route after DNS cutover
// (helpmateai.xyz/privacy-policy → host rewrite → /landing/privacy-policy
// → this page) instead of a self-referential 404. Replace with the real
// privacy policy when copy is ready.

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy — Helpmate AI",
  description: "Helpmate AI privacy policy.",
};

export default function PrivacyPolicyPage() {
  return (
    <section className="l-sec">
      <div className="l-sec-inner narrow">
        <div className="l-sec-head">
          <div className="eyebrow">Privacy</div>
          <h2>Privacy Policy</h2>
          <p>
            A complete privacy policy is on the way. In the meantime, you
            can reach out via the contact links in the footer.
          </p>
        </div>
      </div>
    </section>
  );
}
