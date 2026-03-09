import { SetupsClient } from "@/components/setups/setups-client";
import { getProjectSnapshot } from "@/lib/server/project-snapshot-store";
import type { Trajectory } from "@/lib/types";

type ProjectSetupsPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectSetupsPage({
  params,
}: ProjectSetupsPageProps) {
  const { project } = await params;
  const snapshot = await getProjectSnapshot(project);
  const allTraces = snapshot.trajectories;
  return (
    <SetupsClient
      allTraces={dedupeTrajectoriesById(allTraces)}
      project={project}
    />
  );
}

function dedupeTrajectoriesById(traces: Trajectory[]): Trajectory[] {
  const deduped = new Map<string, Trajectory>();
  for (const trace of traces) {
    deduped.set(trace.id, trace);
  }
  return [...deduped.values()];
}
