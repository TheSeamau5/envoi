/**
 * Compare layout — server component.
 * Fetches all trajectories once and wraps sub-routes in the
 * shared CompareProvider (state) + CompareShell (sidebar + tabs).
 *
 * The layout persists across /compare/curves, /compare/milestones,
 * and /compare/suites navigations — sidebar state survives tab changes.
 */

import type { ReactNode } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";

export default async function CompareLayout({ children }: { children: ReactNode }) {
  const allTraces = await getAllTrajectories();
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  return (
    <CompareProvider allTraces={activeTraces}>
      <CompareShell>{children}</CompareShell>
    </CompareProvider>
  );
}
