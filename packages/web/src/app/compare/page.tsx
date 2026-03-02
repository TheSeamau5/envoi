/**
 * Compare index — redirects to the default sub-route (curves).
 */

import { redirect } from "next/navigation";

export default function ComparePage() {
  redirect("/compare/curves");
}
