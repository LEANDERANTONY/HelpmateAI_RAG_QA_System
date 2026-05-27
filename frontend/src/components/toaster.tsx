"use client";

import { Toaster as SonnerToaster } from "sonner";

export function Toaster() {
  return (
    <SonnerToaster
      className="h-toaster"
      position="bottom-right"
      theme="dark"
      visibleToasts={4}
      offset={20}
      // mobileOffset lifts the toast above the sticky ask-group on
      // narrow viewports. Without it, the bottom-right toast at
      // bottom=20px overlaps the ~202px-tall ask-group at the bottom
      // of the workspace — surfaced in the mobile UI test where an
      // error toast pinned itself across the next answer card's
      // citation pills. The 220px clears the ask-group + leaves a
      // small visual breathing room above it. Sonner treats viewports
      // below ~600px as mobile for this purpose.
      mobileOffset={{ bottom: 220, right: 12 }}
      gap={10}
      // Global cap. Defensive override of sonner's per-type defaults:
      // toast.error in some sonner versions extends the duration when
      // an `action` button is attached (the "give the user time to
      // hit Retry" pattern), which on mobile leaves the error pinned
      // over content for tens of seconds. The mobile test caught a
      // "Something went wrong" toast persisting ~60s. The duration
      // prop is enforced uniformly for every toast type via this prop;
      // notifyApiError / notifyError pass nothing per-call so the
      // global wins.
      duration={6000}
      toastOptions={{
        classNames: {
          toast: "h-toast",
          title: "h-toast-title",
          description: "h-toast-description",
          actionButton: "h-toast-action",
          closeButton: "h-toast-close",
          error: "h-toast-error",
          success: "h-toast-success",
          info: "h-toast-info",
          warning: "h-toast-warning",
        },
      }}
    />
  );
}
