/** Trajectory Detail page — cache-first client shell. */

import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetailPageClient } from "@/components/trajectory/trajectory-detail-page-client";
import { requireActiveProject } from "@/lib/server/project-context";

type TrajectoryPageProps = {
  params: Promise<{ id: string }>;
};

export default async function TrajectoryPage({ params }: TrajectoryPageProps) {
  const project = await requireActiveProject();
  const { id } = await params;
  const { rightPanelOpen, dividerPct, groupByTurn } = await readLayoutCookies();

  return (
    <TrajectoryDetailPageClient
      project={project}
      trajectoryId={id}
      initialRightPanelOpen={rightPanelOpen}
      initialDividerPct={dividerPct}
      initialGroupByTurn={groupByTurn}
    />
  );
}
