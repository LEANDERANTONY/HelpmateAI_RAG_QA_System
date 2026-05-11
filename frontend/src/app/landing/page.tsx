import { LandingClaims } from "@/components/landing/editorial-claims";
import { LandingFinalCTA } from "@/components/landing/final-cta";
import { LandingFlow } from "@/components/landing/flow-carousel";
import { LandingHero } from "@/components/landing/hero";
import { LandingValidation } from "@/components/landing/validation";

export default function LandingPage() {
  return (
    <>
      <LandingHero />
      <LandingValidation />
      <LandingClaims />
      <LandingFlow />
      <LandingFinalCTA />
    </>
  );
}
