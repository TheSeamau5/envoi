import { TrajectoryList } from "@/components/trajectory/trajectory-list";

type ProjectTrajectoryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectTrajectoryPage({
  params,
}: ProjectTrajectoryPageProps) {
  const { project } = await params;
  return <TrajectoryList trajectories={[]} project={project} />;
}
