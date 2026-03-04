import { redirect } from "next/navigation";

type ProjectHomePageProps = {
  params: Promise<{ project: string }>;
};

export default async function ProjectHomePage({
  params,
}: ProjectHomePageProps) {
  const { project } = await params;
  redirect(`/project/${encodeURIComponent(project)}/compare/curves`);
}
