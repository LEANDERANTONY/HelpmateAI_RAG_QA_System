"use client";

import type { ReactNode } from "react";

import type { AuthUserSummary } from "@/lib/auth";
import { AuthSidebar } from "@/components/auth-sidebar";

type WorkspaceShellProps = {
  children: ReactNode;
  user: AuthUserSummary | null;
};

export function WorkspaceShell({ children, user }: WorkspaceShellProps) {
  return (
    <div className="workspace-layout mx-auto flex w-full max-w-[1440px] gap-5 px-4 py-5 md:px-6 lg:px-8">
      <AuthSidebar user={user} />
      <div className="workspace-main min-w-0 flex-1">{children}</div>
    </div>
  );
}
