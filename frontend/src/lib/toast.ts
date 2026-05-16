"use client";

import { toast } from "sonner";

import {
  type ApiOperation,
  isApiError,
  messageForApiError,
} from "@/lib/api-errors";

type NotifyApiErrorOptions = {
  onRetry?: () => void;
};

// We never blindly navigate to a URL handed back in a response body.
// Only follow the backend's `upgrade_url` if it's a plain https:// link
// (or http://localhost in dev); anything else (misconfigured, tampered,
// a javascript: scheme) falls back to the known pricing page.
const DEFAULT_UPGRADE_URL = "https://helpmateai.xyz/pricing";

function safeUpgradeUrl(url: string | null | undefined): string {
  if (!url) return DEFAULT_UPGRADE_URL;
  try {
    const u = new URL(url);
    if (u.protocol === "https:") return u.toString();
    if (
      u.protocol === "http:" &&
      (u.hostname === "localhost" || u.hostname === "127.0.0.1")
    ) {
      return u.toString();
    }
  } catch {
    /* not a valid absolute URL — fall through to the default */
  }
  return DEFAULT_UPGRADE_URL;
}

export function notifyApiError(
  err: unknown,
  op: ApiOperation,
  opts: NotifyApiErrorOptions = {},
) {
  const copy = messageForApiError(err, op);

  // Tier/quota errors carry `upgradeUrl` (string | null). The action is
  // a "See plans" link (not a retry) and shows regardless of
  // retriability, styled with the mint accent via h-toast-action-upgrade.
  if (copy.upgradeUrl !== undefined && copy.action) {
    const target = safeUpgradeUrl(copy.upgradeUrl);
    toast.error(copy.title, {
      description: copy.body,
      action: {
        label: copy.action,
        onClick: () => {
          window.open(target, "_blank", "noopener,noreferrer");
        },
      },
      classNames: { actionButton: "h-toast-action-upgrade" },
    });
    return;
  }

  const retriable = isApiError(err) ? err.retriable : true;
  const showAction = Boolean(copy.action && retriable && opts.onRetry);

  toast.error(copy.title, {
    description: copy.body,
    action: showAction
      ? {
          label: copy.action as string,
          onClick: () => {
            opts.onRetry?.();
          },
        }
      : undefined,
  });
}

export function notifyError(title: string, body?: string) {
  toast.error(title, { description: body });
}

export function notifyInfo(title: string, body?: string) {
  toast(title, { description: body });
}

/**
 * Paid-feature / upgrade nudge carrying the SAME mint "See plans" CTA
 * the quota toasts use (h-toast-action-upgrade → safeUpgradeUrl), so
 * every "this is a paid feature" surface gets one consistent upgrade
 * path. Neutral toast (not toast.error) — a client-side paid-feature
 * pre-check isn't an error. There's no backend upgrade_url on the
 * client gate, so it points at the default pricing page.
 */
export function notifyUpgrade(title: string, body?: string) {
  const target = safeUpgradeUrl(null);
  toast(title, {
    description: body,
    action: {
      label: "See plans",
      onClick: () => {
        window.open(target, "_blank", "noopener,noreferrer");
      },
    },
    classNames: { actionButton: "h-toast-action-upgrade" },
  });
}

export function notifySuccess(title: string, body?: string) {
  toast.success(title, { description: body });
}
