import { DifficultyPageClient } from "@/components/difficulty/difficulty-page-client";
import { getDifficultyData } from "@/lib/server/data";

type ProjectDifficultyPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectDifficultyPage({
  params,
}: ProjectDifficultyPageProps) {
  const { project } = await params;
  const cells = await getDifficultyData(project);
  return <DifficultyPageClient project={project} initialCells={cells} />;
}
