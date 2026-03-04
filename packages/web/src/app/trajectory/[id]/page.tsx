/**
 * Trajectory Detail page — server component.
 * Resolves trajectory by ID from data layer (S3 or mock fallback).
 */

import { notFound } from "next/navigation";
import { getTrajectoryById } from "@/lib/server/data";
import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetail } from "@/components/trajectory/trajectory-detail";
import { requireActiveProject } from "@/lib/server/project-context";

type TrajectoryPageProps = {
  params: Promise<{ id: string }>;
};

export default async function TrajectoryPage({ params }: TrajectoryPageProps) {
  const project = await requireActiveProject();
  const { id } = await params;
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
