/**
 * Trajectories listing page — server component.
 * Fetches all trajectories and passes them to a client component
 * that handles tab switching between active and failed runs.
 */

import { getAllTrajectories } from "@/lib/server/data";
import { TrajectoryList } from "@/components/trajectory/trajectory-list";

export default async function TrajectoryListPage() {
  const allTraces = await getAllTrajectories();
  return <TrajectoryList trajectories={allTraces} />;
}
