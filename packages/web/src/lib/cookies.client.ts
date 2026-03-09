/**
 * Client-side cookie writer for layout-critical persisted state.
 * Called from client components after state changes so the next SSR pass
 * picks up the user's preference via cookies.ts (server-side reader).
 */

const COOKIE_RIGHT_PANEL = "envoi:detail-right-panel-open";
const COOKIE_DIVIDER_PCT = "envoi:detail-divider-pct";
const COOKIE_SIDEBAR_COLLAPSED = "envoi:sidebar-collapsed";
const COOKIE_GROUP_BY_TURN = "envoi:trajectory-group-by-turn";
const COOKIE_PROJECT = "envoi:project";
const COOKIE_CHAT_HAS_MESSAGES = "envoi:chat-has-messages";
const COOKIE_COMPARE_TRACE_COLORS = "envoi:compare-trace-colors";

export type LayoutCookieKey =
  | "rightPanelOpen"
  | "dividerPct"
  | "sidebarCollapsed"
  | "groupByTurn"
  | "chatHasMessages";

/** Write a layout cookie. Max-age: 1 year, SameSite=Lax, path=/. */
export function setLayoutCookie(
  key: LayoutCookieKey,
  value: boolean | number,
): void {
  const cookieNames: Record<string, string> = {
    rightPanelOpen: COOKIE_RIGHT_PANEL,
    dividerPct: COOKIE_DIVIDER_PCT,
    sidebarCollapsed: COOKIE_SIDEBAR_COLLAPSED,
    groupByTurn: COOKIE_GROUP_BY_TURN,
    chatHasMessages: COOKIE_CHAT_HAS_MESSAGES,
  };
  const name = cookieNames[key];
  if (!name) {
    return;
  }
  const encoded = encodeURIComponent(String(value));
  document.cookie = `${name}=${encoded}; path=/; max-age=31536000; SameSite=Lax`;
}

/** Write the active project cookie. Max-age: 1 year, SameSite=Lax, path=/. */
export function setProjectCookie(name: string): void {
  const trimmed = name.trim();
  if (!trimmed) {
    return;
  }
  const encoded = encodeURIComponent(trimmed);
  document.cookie = `${COOKIE_PROJECT}=${encoded}; path=/; max-age=31536000; SameSite=Lax`;
}

/** Mirror compare trace color state into a cookie for SSR/client alignment. */
export function setCompareTraceColorsCookie(
  colorMap: Record<string, number>,
): void {
  const encoded = encodeURIComponent(JSON.stringify(colorMap));
  document.cookie = `${COOKIE_COMPARE_TRACE_COLORS}=${encoded}; path=/; max-age=31536000; SameSite=Lax`;
}
