import { Sidebar } from "@/components/sidebar";
import { BottomBar } from "@/components/bottom-bar";
import { ProjectCookieSync } from "@/components/project/project-cookie-sync";
import { readLayoutCookies } from "@/lib/cookies";

type ProjectLayoutProps = {
  children: React.ReactNode;
  params: Promise<{ project: string }>;
};

export default async function ProjectLayout({
  children,
  params,
}: ProjectLayoutProps) {
  const [{ sidebarCollapsed }, { project }] = await Promise.all([
    readLayoutCookies(),
    params,
  ]);

  return (
    <div className="flex h-screen overflow-hidden">
      <ProjectCookieSync project={project} />
      <Sidebar initialCollapsed={sidebarCollapsed} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {children}
        </div>
        <BottomBar />
      </div>
    </div>
  );
}
