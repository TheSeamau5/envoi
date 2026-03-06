/** Setups page — cache-first client shell. */

import { SetupsClient } from "@/components/setups/setups-client";
import { requireActiveProject } from "@/lib/server/project-context";

export default async function SetupsPage() {
  const project = await requireActiveProject();
  return <SetupsClient allTraces={[]} project={project} />;
}
