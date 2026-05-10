"use client";

import type { ReactNode } from "react";

type WorkspaceShellProps = {
  children: ReactNode;
};

export function WorkspaceShell({ children }: WorkspaceShellProps) {
  return (
    <div className="h-full w-full">
      <div className="h-full min-w-0">{children}</div>
    </div>
  );
}
