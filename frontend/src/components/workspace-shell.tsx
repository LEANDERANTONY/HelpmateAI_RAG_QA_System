"use client";

import type { ReactNode } from "react";

type WorkspaceShellProps = {
  children: ReactNode;
};

export function WorkspaceShell({ children }: WorkspaceShellProps) {
  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 py-5 md:px-6 lg:px-8">
      <div className="min-w-0">{children}</div>
    </div>
  );
}
