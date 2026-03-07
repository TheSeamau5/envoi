import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetailPageClient } from "@/components/trajectory/trajectory-detail-page-client";
import { getTrajectoryById } from "@/lib/server/data";

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
      getTrajectoryById(id, { project }),
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
