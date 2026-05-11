"use client";

import { useEffect, useState } from "react";

// On the production apex (helpmateai.xyz) the host rewrite in
// next.config.ts maps "/" → "/landing" internally, so a back-to-
// landing link wired to "/" works correctly there. Everywhere else
// — localhost dev, Vercel preview deploys, the app.* subdomain — the
// root path "/" serves the workspace, so the landing only lives at
// the explicit "/landing" route.
//
// SSR defaults to "/" (the helpmateai.xyz behavior). After hydration
// we check the host and bump to "/landing" if we're not on the apex.
// React state pattern, no hydration warning.
export function useLandingHref() {
  const [href, setHref] = useState("/");
  useEffect(() => {
    if (window.location.host !== "helpmateai.xyz") {
      setHref("/landing");
    }
  }, []);
  return href;
}
