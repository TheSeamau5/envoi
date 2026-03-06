/** Portfolio Dashboard page — cache-first client shell. */

import { PortfolioPageClient } from "@/components/portfolio/portfolio-page-client";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function PortfolioPage() {
  const project = await requireActiveProject();
  return <PortfolioPageClient project={project} />;
}
