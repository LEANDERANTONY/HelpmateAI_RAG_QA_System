import type { Metadata } from "next";
import { LandingFooter } from "@/components/landing/footer";
import { LandingTopbar } from "@/components/landing/topbar";
import "./landing.css";

export const metadata: Metadata = {
  title: "Helpmate AI — Answers your documents can vouch for.",
  description:
    "Helpmate reads the policy, the contract, the manual — and answers with citations to the exact paragraph it relied on. When the source is silent, it abstains.",
};

export default function LandingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="h-shell l-shell">
      <LandingTopbar />
      <main>{children}</main>
      <LandingFooter />
    </div>
  );
}
