/** SQL Console page — cache-first client shell. */

import { QueryPageClient } from "@/components/query/query-page-client";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function QueryPage() {
  const project = await requireActiveProject();
  return <QueryPageClient project={project} />;
}
