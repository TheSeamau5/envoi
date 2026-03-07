import { PortfolioPageClient } from "@/components/portfolio/portfolio-page-client";
import {
  getParetoData,
  getPortfolioData,
  getPortfolioEnvironmentData,
} from "@/lib/server/data";

type ProjectPortfolioPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectPortfolioPage({
  params,
}: ProjectPortfolioPageProps) {
  const { project } = await params;
  const [rows, environmentRows, paretoPoints] = await Promise.all([
    getPortfolioData(project),
    getPortfolioEnvironmentData(project),
    getParetoData(undefined, project),
  ]);
  return (
    <PortfolioPageClient
      project={project}
      initialData={{ rows, environmentRows, paretoPoints }}
    />
  );
}
