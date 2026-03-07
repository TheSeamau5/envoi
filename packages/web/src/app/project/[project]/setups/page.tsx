import { SetupsClient } from "@/components/setups/setups-client";
import { getAllTrajectories } from "@/lib/server/data";
import type { Trajectory } from "@/lib/types";

type ProjectSetupsPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectSetupsPage({
  params,
}: ProjectSetupsPageProps) {
  const { project } = await params;
  return (
    <SetupsClient
      allTraces={dedupeTrajectoriesById(await getAllTrajectories({ project }))}
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
