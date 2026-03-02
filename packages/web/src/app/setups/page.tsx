/**
 * Setups page — server component.
 * Fetches all trajectories and renders the Setup Compare view.
 * This is a standalone page separate from the trace comparison flow.
 */

import { getAllTrajectories } from "@/lib/server/data";
import { SetupsClient } from "@/components/setups/setups-client";

export default async function SetupsPage() {
  const allTraces = await getAllTrajectories();
  const activeTraces = allTraces.filter((trace) => trace.finalPassed > 0);

  return <SetupsClient allTraces={activeTraces} />;
}
