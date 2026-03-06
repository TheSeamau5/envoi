import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetailPageClient } from "@/components/trajectory/trajectory-detail-page-client";

type ProjectTrajectoryDetailPageProps = {
  params: Promise<{ project: string; id: string }>;
};

export default async function ProjectTrajectoryDetailPage({
  params,
}: ProjectTrajectoryDetailPageProps) {
  const { project, id } = await params;
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
