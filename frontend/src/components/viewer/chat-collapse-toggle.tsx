"use client";

// ChatCollapseToggle — the single affordance that toggles the chat
// panel between expanded (45/55 grid) and collapsed (48px strip).
//
// Two visual states driven by the same button, differentiated via
// data-collapsed and CSS:
//
//   Expanded: a narrow 24px chevron pinned to the right edge of
//   the chat aside, hover-revealed. Click → collapse.
//
//   Collapsed: the same button expands to fill the 48px chat
//   column. Vertical "CHAT" label below the chevron makes the
//   purpose obvious at a glance. Click anywhere on the strip
//   → expand.
//
// Renders nothing outside Read Mode and on mobile (mobile has its
// own equivalent via the FULL snap, so this button would be
// redundant / visually competing).
//
// The button is positioned relative to .h-conv (which sets
// position:relative when in Read Mode). When the grid column
// collapses, the button absorbs the full column width.

import { useChatCollapsed, useReadModeActions, useReadModeStatus } from "@/lib/read-mode-state";

export function ChatCollapseToggle() {
  const mode = useReadModeStatus();
  const collapsed = useChatCollapsed();
  const { toggleChatCollapsed } = useReadModeActions();

  if (mode !== "read") {
    return null;
  }

  return (
    <button
      type="button"
      className="h-chat-collapse-toggle"
      data-collapsed={collapsed}
      onClick={toggleChatCollapsed}
      aria-label={collapsed ? "Expand chat" : "Collapse chat"}
      aria-pressed={collapsed}
      title={collapsed ? "Expand chat" : "Collapse chat"}
    >
      <span className="h-chat-collapse-glyph" aria-hidden>
        {collapsed ? "›" : "‹"}
      </span>
      {collapsed ? (
        <span className="h-chat-collapse-label" aria-hidden>
          CHAT
        </span>
      ) : null}
    </button>
  );
}
