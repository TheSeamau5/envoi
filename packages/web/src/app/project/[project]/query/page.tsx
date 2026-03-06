import { QueryPageClient } from "@/components/query/query-page-client";

type ProjectQueryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectQueryPage({
  params,
}: ProjectQueryPageProps) {
  const { project } = await params;
  return <QueryPageClient project={project} />;
}
