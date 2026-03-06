import { SetupsClient } from "@/components/setups/setups-client";

type ProjectSetupsPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectSetupsPage({
  params,
}: ProjectSetupsPageProps) {
  const { project } = await params;
  return <SetupsClient allTraces={[]} project={project} />;
}
