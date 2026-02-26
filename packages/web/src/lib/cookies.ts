/**
 * Server-side cookie reader for layout-critical persisted state.
 *
 * These values are read during SSR so the first paint matches the client,
 * eliminating FOUC on panel open/close and divider position.
 *
 * Server-only — do NOT import this from "use client" components.
 * Client components should import setLayoutCookie from cookies.client.ts.
 */

import { cookies } from "next/headers";

const COOKIE_RIGHT_PANEL = "envoi:detail-right-panel-open";
const COOKIE_DIVIDER_PCT = "envoi:detail-divider-pct";

export type LayoutCookies = {
  rightPanelOpen: boolean;
  dividerPct: number;
};

/** Read layout preferences from cookies (server-side only). */
export async function readLayoutCookies(): Promise<LayoutCookies> {
  const jar = await cookies();

  let rightPanelOpen = true;
  let dividerPct = 42;

  try {
    const panelCookie = jar.get(COOKIE_RIGHT_PANEL);
    if (panelCookie) {
      rightPanelOpen = panelCookie.value === "true";
    }
  } catch {
    // cookie unavailable — use default
  }

  try {
    const dividerCookie = jar.get(COOKIE_DIVIDER_PCT);
    if (dividerCookie) {
      const parsed = Number(dividerCookie.value);
      if (!Number.isNaN(parsed) && parsed >= 25 && parsed <= 75) {
        dividerPct = parsed;
      }
    }
  } catch {
    // cookie unavailable — use default
  }

  return { rightPanelOpen, dividerPct };
}
