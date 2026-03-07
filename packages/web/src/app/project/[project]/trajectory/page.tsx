import { TrajectoryList } from "@/components/trajectory/trajectory-list";
import { getAllTrajectories } from "@/lib/server/data";

type ProjectTrajectoryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectTrajectoryPage({
  params,
}: ProjectTrajectoryPageProps) {
  const { project } = await params;
  const trajectories = await getAllTrajectories({ project });
  return <TrajectoryList trajectories={trajectories} project={project} />;
}
