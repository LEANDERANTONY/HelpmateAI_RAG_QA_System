import { AppWorkspace } from "@/components/app-workspace";
import { WorkspaceShell } from "@/components/workspace-shell";
import { toAuthUserSummary } from "@/lib/auth";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export default async function WorkspacePage() {
  let user = null;

  if (isSupabaseConfigured()) {
    const supabase = await createClient();
    const {
      data: { user: sessionUser },
    } = await supabase.auth.getUser();
    user = sessionUser;
  }

  return (
    <main className="workspace-page flex-1">
      <WorkspaceShell>
        <AppWorkspace user={toAuthUserSummary(user)} />
      </WorkspaceShell>
    </main>
  );
}
