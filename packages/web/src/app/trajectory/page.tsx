/**
 * Trajectories listing page — server component.
 * Fetches all trajectories and passes them to a client component
 * that handles tab switching between active and failed runs.
 */

import { getAllTrajectories } from "@/lib/server/data";
import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function TrajectoryListPage() {
  const project = await requireActiveProject();
  const allTraces = await getAllTrajectories({ project });
  return <TrajectoryList trajectories={allTraces} project={project} />;
}
