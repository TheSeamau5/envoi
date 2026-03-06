/** Difficulty Heatmap page — cache-first client shell. */

import { DifficultyPageClient } from "@/components/difficulty/difficulty-page-client";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function DifficultyPage() {
  const project = await requireActiveProject();
  return <DifficultyPageClient project={project} />;
}
