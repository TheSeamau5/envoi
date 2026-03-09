import type { ReactNode } from "react";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";
import { readLayoutCookies } from "@/lib/cookies";
import { getProjectSnapshot } from "@/lib/server/project-snapshot-store";

type ProjectCompareLayoutProps = {
  children: ReactNode;
  params: Promise<{ project: string }>;
};

export default async function ProjectCompareLayout({
  children,
  params,
}: ProjectCompareLayoutProps) {
  const { project } = await params;
  const [snapshot, { compareTraceColors }] = await Promise.all([
    getProjectSnapshot(project),
    readLayoutCookies(),
  ]);
  const allTraces = snapshot.trajectories;

  return (
    <CompareProvider
      allTraces={allTraces}
      initialColorMap={compareTraceColors}
      project={project}
    >
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
