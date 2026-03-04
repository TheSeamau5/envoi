/**
 * Compare index — redirects to the default sub-route (curves).
 */

import { redirect } from "next/navigation";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function ComparePage() {
  const project = await requireActiveProject();
  redirect(`/project/${encodeURIComponent(project)}/compare/curves`);
}
