/**
 * Trajectories listing page — server component.
 * Fetches all trajectories and passes them to a client component
 * that handles tab switching between active and failed runs.
 */

import { Suspense } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function TrajectoryListPage() {
  const project = await requireActiveProject();

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading trajectories..." />}>
      <TrajectoryListContent project={project} />
    </Suspense>
  );
}

async function TrajectoryListContent({ project }: { project: string }) {
  const allTraces = await getAllTrajectories({ project });
  return <TrajectoryList trajectories={allTraces} project={project} />;
}
