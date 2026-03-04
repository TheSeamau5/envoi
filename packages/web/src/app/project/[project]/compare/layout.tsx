import { Suspense } from "react";
import type { ReactNode } from "react";
import { getAllTrajectories } from "@/lib/server/data";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";
import { LoadingSkeleton } from "@/components/loading-skeleton";

type ProjectCompareLayoutProps = {
  children: ReactNode;
  params: Promise<{ project: string }>;
};

export default async function ProjectCompareLayout({
  children,
  params,
}: ProjectCompareLayoutProps) {
  const { project } = await params;

  return (
    <Suspense fallback={<LoadingSkeleton message="Loading compare data..." />}>
      <CompareContent project={project}>{children}</CompareContent>
    </Suspense>
  );
}

async function CompareContent({
  project,
  children,
}: {
  project: string;
  children: ReactNode;
}) {
  const allTraces = await getAllTrajectories({ project });
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  return (
    <CompareProvider allTraces={activeTraces} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
