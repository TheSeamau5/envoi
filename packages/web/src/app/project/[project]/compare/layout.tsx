import type { ReactNode } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";

type ProjectCompareLayoutProps = {
  children: ReactNode;
  params: Promise<{ project: string }>;
};

export default async function ProjectCompareLayout({
  children,
  params,
}: ProjectCompareLayoutProps) {
  const { project } = await params;
  const allTraces = await getAllTrajectories({ project });
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  return (
    <CompareProvider allTraces={activeTraces} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
