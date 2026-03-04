import { getAllTrajectories } from "@/lib/server/data";
import { TrajectoryList } from "@/components/trajectory/trajectory-list";

type ProjectTrajectoryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectTrajectoryPage({
  params,
}: ProjectTrajectoryPageProps) {
  const { project } = await params;
  const allTraces = await getAllTrajectories({ project });
  return <TrajectoryList trajectories={allTraces} project={project} />;
}
