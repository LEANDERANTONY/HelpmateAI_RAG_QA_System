import type { ReactNode } from "react";

type ErrorStateProps = {
  title?: string;
  message: string;
  action?: ReactNode;
};

export function ErrorState({
  title = "Something's not right",
  message,
  action,
}: ErrorStateProps) {
  return (
    <div className="h-error-state" role="status">
      <p className="h-error-state-title">{title}</p>
      <p className="h-error-state-body">{message}</p>
      {action ? <div className="h-error-state-action">{action}</div> : null}
    </div>
  );
}
