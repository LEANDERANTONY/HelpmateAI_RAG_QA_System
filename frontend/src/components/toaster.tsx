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
      gap={10}
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
