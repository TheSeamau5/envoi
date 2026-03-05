/**
 * Trajectory Detail page — server component.
 * Resolves trajectory by ID from data layer (S3 or mock fallback).
 */

import { Suspense } from "react";
import { notFound } from "next/navigation";
import { getTrajectoryById } from "@/lib/server/data";
import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetail } from "@/components/trajectory/trajectory-detail";
import { TrajectoryDetailSkeleton } from "@/components/trajectory/trajectory-detail-skeleton";
import { requireActiveProject } from "@/lib/server/project-context";

type TrajectoryPageProps = {
  params: Promise<{ id: string }>;
};

export default async function TrajectoryPage({ params }: TrajectoryPageProps) {
  const project = await requireActiveProject();
  const { id } = await params;

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
  const [trajectory, { rightPanelOpen, dividerPct, groupByTurn }] =
    await Promise.all([
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
      initialGroupByTurn={groupByTurn}
    />
  );
}
