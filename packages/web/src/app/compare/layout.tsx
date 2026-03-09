/** Compare layout — persistent cache-first client shell. */

import type { ReactNode } from "react";
import { CompareProvider } from "@/components/compare/compare-context";
import { CompareShell } from "@/components/compare/compare-shell";
import { readLayoutCookies } from "@/lib/cookies";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function CompareLayout({
  children,
}: {
  children: ReactNode;
}) {
  const [{ compareTraceColors }, project] = await Promise.all([
    readLayoutCookies(),
    requireActiveProject(),
  ]);

  return (
    <CompareProvider
      allTraces={[]}
      initialColorMap={compareTraceColors}
      project={project}
    >
      <CompareShell project={project}>{children}</CompareShell>
    </CompareProvider>
  );
}
