import { notFound } from "next/navigation";
import { getTrajectoryById } from "@/lib/server/data";
import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetail } from "@/components/trajectory/trajectory-detail";

type ProjectTrajectoryDetailPageProps = {
  params: Promise<{ project: string; id: string }>;
};

export default async function ProjectTrajectoryDetailPage({
  params,
}: ProjectTrajectoryDetailPageProps) {
  const { project, id } = await params;
  const trajectory = await getTrajectoryById(id, { project });

  if (!trajectory) {
    notFound();
  }

  const { rightPanelOpen, dividerPct } = await readLayoutCookies();

  return (
    <TrajectoryDetail
      trajectory={trajectory}
      project={project}
      initialRightPanelOpen={rightPanelOpen}
      initialDividerPct={dividerPct}
    />
  );
}
