/**
 * Setups page — server component.
 * Fetches all active trajectories with full commit data and renders
 * the Setup Compare view.
 *
 * Full trajectories are stripped to only the fields SetupCompare uses
 * (commits with minutesElapsed/totalPassed/suiteState) so the RSC
 * payload stays small (~50 KB instead of ~40 MB).
 */

import { Suspense } from "react";
import type { Trajectory } from "@/lib/types";
import { getAllTrajectories, getTrajectoryById } from "@/lib/server/data";
import { SetupCompare } from "@/components/compare/setup-compare";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { requireActiveProject } from "@/lib/server/project-context";

/** Strip a trajectory to only the fields SetupCompare needs */
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

export default async function SetupsPage() {
  const project = await requireActiveProject();

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
