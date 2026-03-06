/** Trajectories listing page — cache-first client shell. */

import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function TrajectoryListPage() {
  const project = await requireActiveProject();
  return <TrajectoryList trajectories={[]} project={project} />;
}
