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
import { requireActiveProject } from "@/lib/server/project-context";

export default async function CompareLayout({
  children,
}: {
  children: ReactNode;
}) {
  const project = await requireActiveProject();
  const allTraces = await getAllTrajectories({ project });
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  return (
    <CompareProvider allTraces={activeTraces} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
