/** Compare layout — persistent cache-first client shell. */

import type { ReactNode } from "react";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function CompareLayout({
  children,
}: {
  children: ReactNode;
}) {
  const project = await requireActiveProject();

  return (
    <CompareProvider allTraces={[]} project={project}>
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
