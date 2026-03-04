import { ProjectList } from "@/components/project/project-list";
import { readLayoutCookies } from "@/lib/cookies";
import { getProjects } from "@/lib/server/projects";

/** Root page — project list and project switcher. */
export default async function RootPage() {
  const [{ project }, projects] = await Promise.all([
    readLayoutCookies(),
    getProjects(),
  ]);

  return <ProjectList projects={projects} activeProject={project} />;
}
