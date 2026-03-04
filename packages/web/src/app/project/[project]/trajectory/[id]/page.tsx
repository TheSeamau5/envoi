import { Suspense } from "react";
import { notFound } from "next/navigation";
import { getTrajectoryById } from "@/lib/server/data";
import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetail } from "@/components/trajectory/trajectory-detail";
import { TrajectoryDetailSkeleton } from "@/components/trajectory/trajectory-detail-skeleton";

type ProjectTrajectoryDetailPageProps = {
  params: Promise<{ project: string; id: string }>;
};

export default async function ProjectTrajectoryDetailPage({
  params,
}: ProjectTrajectoryDetailPageProps) {
  const { project, id } = await params;

  return (
    <Suspense fallback={<TrajectoryDetailSkeleton />}>
      <TrajectoryContent project={project} id={id} />
    </Suspense>
  );
}

async function TrajectoryContent({
  project,
  id,
}: {
  project: string;
  id: string;
}) {
  const [trajectory, { rightPanelOpen, dividerPct }] = await Promise.all([
    getTrajectoryById(id, { project }),
    readLayoutCookies(),
  ]);

  if (!trajectory) {
    notFound();
  }

  return (
    <TrajectoryDetail
      trajectory={trajectory}
      project={project}
      initialRightPanelOpen={rightPanelOpen}
      initialDividerPct={dividerPct}
    />
  );
}
