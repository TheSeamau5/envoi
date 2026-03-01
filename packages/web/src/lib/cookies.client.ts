/**
 * Client-side cookie writer for layout-critical persisted state.
 * Called from client components after state changes so the next SSR pass
 * picks up the user's preference via cookies.ts (server-side reader).
 */

const COOKIE_RIGHT_PANEL = "envoi:detail-right-panel-open";
const COOKIE_DIVIDER_PCT = "envoi:detail-divider-pct";
const COOKIE_SIDEBAR_COLLAPSED = "envoi:sidebar-collapsed";

/** Write a layout cookie. Max-age: 1 year, SameSite=Lax, path=/. */
export function setLayoutCookie(
  key: "rightPanelOpen" | "dividerPct" | "sidebarCollapsed",
  value: boolean | number,
): void {
  const cookieNames: Record<string, string> = {
    rightPanelOpen: COOKIE_RIGHT_PANEL,
    dividerPct: COOKIE_DIVIDER_PCT,
    sidebarCollapsed: COOKIE_SIDEBAR_COLLAPSED,
  };
  const name = cookieNames[key];
  if (!name) {
    return;
  }
  const encoded = encodeURIComponent(String(value));
  document.cookie = `${name}=${encoded}; path=/; max-age=31536000; SameSite=Lax`;
}
