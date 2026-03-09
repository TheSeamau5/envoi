import { DifficultyPageClient } from "@/components/difficulty/difficulty-page-client";
import { getDifficultyCellsFromSnapshot } from "@/lib/server/project-snapshot-store";

type ProjectDifficultyPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectDifficultyPage({
  params,
}: ProjectDifficultyPageProps) {
  const { project } = await params;
  const cells = await getDifficultyCellsFromSnapshot(project);
  return <DifficultyPageClient project={project} initialCells={cells} />;
}
