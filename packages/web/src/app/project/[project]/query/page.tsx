import { QueryPageClient } from "@/components/query/query-page-client";
import { getSchemaInfo } from "@/lib/server/data";

type ProjectQueryPageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectQueryPage({
  params,
}: ProjectQueryPageProps) {
  const { project } = await params;
  const schema = await getSchemaInfo(project);
  return <QueryPageClient project={project} initialSchema={schema} />;
}
