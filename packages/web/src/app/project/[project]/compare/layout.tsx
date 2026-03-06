import type { ReactNode } from "react";
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

  return (
    <CompareProvider allTraces={[]} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
