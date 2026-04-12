import { AppWorkspace } from "@/components/app-workspace";

export default function WorkspacePage() {
  return (
    <main className="workspace-page flex-1">
      <div className="mx-auto flex w-full max-w-7xl flex-1 px-6 py-8 md:px-10 lg:px-12">
        <AppWorkspace />
      </div>
    </main>
  );
}
