/**
 * Trajectory Detail page — server component.
 * Resolves trajectory by ID from data layer (S3 or mock fallback).
 */

import { notFound } from "next/navigation";
import { getTrajectoryById } from "@/lib/server/data";
import { readLayoutCookies } from "@/lib/cookies";
import { TrajectoryDetail } from "@/components/trajectory/trajectory-detail";

type TrajectoryPageProps = {
  params: Promise<{ id: string }>;
};

export default async function TrajectoryPage({ params }: TrajectoryPageProps) {
  const { id } = await params;
  const trajectory = await getTrajectoryById(id);

  if (!trajectory) {
    notFound();
  }

  const { rightPanelOpen, dividerPct } = await readLayoutCookies();

  return (
    <TrajectoryDetail
      trajectory={trajectory}
      initialRightPanelOpen={rightPanelOpen}
      initialDividerPct={dividerPct}
    />
  );
}
