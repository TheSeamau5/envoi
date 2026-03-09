import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { getProjectSnapshot } from "@/lib/server/project-snapshot-store";
import type { Trajectory } from "@/lib/types";

type ProjectTrajectoryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectTrajectoryPage({
  params,
}: ProjectTrajectoryPageProps) {
  const { project } = await params;
  const snapshot = await getProjectSnapshot(project);
  const trajectories = dedupeTrajectoriesById(snapshot.trajectories);
  return <TrajectoryList trajectories={trajectories} project={project} />;
}

function dedupeTrajectoriesById(traces: Trajectory[]): Trajectory[] {
  const deduped = new Map<string, Trajectory>();
  for (const trace of traces) {
    deduped.set(trace.id, trace);
  }
  return [...deduped.values()];
}
