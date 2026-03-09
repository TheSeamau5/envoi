import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetailPageClient } from "@/components/trajectory/trajectory-detail-page-client";
import { getTrajectoryDetailFromSnapshot } from "@/lib/server/project-snapshot-store";

type ProjectTrajectoryDetailPageProps = {
  params: Promise<{ project: string; id: string }>;
};

export default async function ProjectTrajectoryDetailPage({
  params,
}: ProjectTrajectoryDetailPageProps) {
  const { project, id } = await params;
  const [{ rightPanelOpen, dividerPct, groupByTurn }, initialTrajectory] =
    await Promise.all([
      readLayoutCookies(),
      getTrajectoryDetailFromSnapshot(project, id),
    ]);

  return (
    <TrajectoryDetailPageClient
      project={project}
      trajectoryId={id}
      initialTrajectory={initialTrajectory}
      initialRightPanelOpen={rightPanelOpen}
      initialDividerPct={dividerPct}
      initialGroupByTurn={groupByTurn}
    />
  );
}
