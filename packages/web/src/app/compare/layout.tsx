/**
 * Compare layout — server component.
 * Fetches all trajectories once and wraps sub-routes in the
 * shared CompareProvider (state) + CompareShell (sidebar + tabs).
 *
 * The layout persists across /compare/curves, /compare/milestones,
 * and /compare/suites navigations — sidebar state survives tab changes.
 */

import { Suspense } from "react";
import type { ReactNode } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function CompareLayout({
  children,
}: {
  children: ReactNode;
}) {
  const project = await requireActiveProject();

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading compare data..." />}>
      <CompareContent project={project}>{children}</CompareContent>
    </Suspense>
  );
}

async function CompareContent({
  project,
  children,
}: {
  project: string;
  children: ReactNode;
}) {
  const allTraces = await getAllTrajectories({ project });

  return (
    <CompareProvider allTraces={allTraces} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
