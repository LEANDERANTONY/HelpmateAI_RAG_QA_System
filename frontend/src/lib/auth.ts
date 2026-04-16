import type { User } from "@supabase/supabase-js";

export type AuthUserSummary = {
  id: string;
  email: string | null;
  displayName: string | null;
  avatarUrl: string | null;
};

export function toAuthUserSummary(user: User | null): AuthUserSummary | null {
  if (!user) {
    return null;
  }

  const displayName =
    typeof user.user_metadata?.full_name === "string"
      ? user.user_metadata.full_name
      : typeof user.user_metadata?.name === "string"
        ? user.user_metadata.name
        : null;

  const avatarUrl =
    typeof user.user_metadata?.avatar_url === "string"
      ? user.user_metadata.avatar_url
      : typeof user.user_metadata?.picture === "string"
        ? user.user_metadata.picture
        : null;

  return {
    id: user.id,
    email: user.email ?? null,
    displayName,
    avatarUrl,
  };
}
