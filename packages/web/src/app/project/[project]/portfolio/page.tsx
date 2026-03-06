import { PortfolioPageClient } from "@/components/portfolio/portfolio-page-client";

type ProjectPortfolioPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectPortfolioPage({
  params,
}: ProjectPortfolioPageProps) {
  const { project } = await params;
  return <PortfolioPageClient project={project} />;
}
