import { redirect } from "next/navigation";

type ProjectComparePageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectComparePage({
  params,
}: ProjectComparePageProps) {
  const { project } = await params;
  redirect(`/project/${encodeURIComponent(project)}/compare/curves`);
}
