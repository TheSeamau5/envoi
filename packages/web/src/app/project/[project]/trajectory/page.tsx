import { Suspense } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { LoadingSkeleton } from "@/components/loading-skeleton";

type ProjectTrajectoryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectTrajectoryPage({
  params,
}: ProjectTrajectoryPageProps) {
  const { project } = await params;

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading trajectories..." />}>
      <TrajectoryListContent project={project} />
    </Suspense>
  );
}

async function TrajectoryListContent({ project }: { project: string }) {
  const allTraces = await getAllTrajectories({ project });
  return <TrajectoryList trajectories={allTraces} project={project} />;
}
