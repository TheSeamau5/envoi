import { Suspense } from "react";
import type { Trajectory } from "@/lib/types";
import { getAllTrajectories, getTrajectoryById } from "@/lib/server/data";
import { SetupCompare } from "@/components/compare/setup-compare";
import { LoadingSkeleton } from "@/components/loading-skeleton";

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
  const allTraces = await getAllTrajectories({ project });
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  const fullTraces = (
    await Promise.all(
      activeTraces.map((trace) => getTrajectoryById(trace.id, { project })),
    )
  ).filter((trace) => trace !== undefined);

  return <SetupCompare allTraces={fullTraces.map(slimTrajectory)} />;
}
