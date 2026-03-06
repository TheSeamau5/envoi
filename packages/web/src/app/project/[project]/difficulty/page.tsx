import { DifficultyPageClient } from "@/components/difficulty/difficulty-page-client";

type ProjectDifficultyPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectDifficultyPage({
  params,
}: ProjectDifficultyPageProps) {
  const { project } = await params;
  return <DifficultyPageClient project={project} />;
}
