import { Suspense } from "react";
import type { Trajectory } from "@/lib/types";
import { getCompareTrajectories } from "@/lib/server/data";
import { SetupsClient } from "@/components/setups/setups-client";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { isTrajectoryActive } from "@/lib/trajectory-state";

function slimTrajectory(trace: Trajectory): Trajectory {
  return {
    ...trace,
    commits: trace.commits.map((commit) => ({
      ...commit,
      steps: [],
      changedFiles: [],
      codeSnapshot: {},
    })),
  };
}

type ProjectSetupsPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectSetupsPage({
  params,
}: ProjectSetupsPageProps) {
  const { project } = await params;

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading setups..." />}>
      <SetupsContent project={project} />
    </Suspense>
  );
}

async function SetupsContent({ project }: { project: string }) {
  const allTraces = await getCompareTrajectories({ project });
  const activeTraces = allTraces
    .filter((trace) => isTrajectoryActive(trace))
    .map(slimTrajectory);

  return <SetupsClient allTraces={activeTraces} project={project} />;
}
