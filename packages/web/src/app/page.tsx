import { ProjectList } from "@/components/project/project-list";
import { readLayoutCookies } from "@/lib/cookies";
import { getProjectsForUi } from "@/lib/server/project-snapshot-store";

/** Root page — project list and project switcher. */
export default async function RootPage() {
  const [{ project }, projects] = await Promise.all([
    readLayoutCookies(),
    getProjectsForUi(),
  ]);

  return <ProjectList projects={projects} activeProject={project} />;
}
