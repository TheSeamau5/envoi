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
import { getCompareTrajectories } from "@/lib/server/data";
import { SetupsClient } from "@/components/setups/setups-client";
import { LoadingSkeleton } from "@/components/loading-skeleton";
import { requireActiveProject } from "@/lib/server/project-context";
import { isTrajectoryActive } from "@/lib/trajectory-state";

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
  const allTraces = await getCompareTrajectories({ project });
  const activeTraces = allTraces
    .filter((trace) => isTrajectoryActive(trace))
    .map(slimTrajectory);

  return <SetupsClient allTraces={activeTraces} project={project} />;
}
