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

export function notifyApiError(
  err: unknown,
  op: ApiOperation,
  opts: NotifyApiErrorOptions = {},
) {
  const copy = messageForApiError(err, op);
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

export function notifySuccess(title: string, body?: string) {
  toast.success(title, { description: body });
}
