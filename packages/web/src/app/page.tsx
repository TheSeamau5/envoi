/**
 * Root page — redirects to /compare.
 */

import { redirect } from "next/navigation";

export default function RootPage() {
  redirect("/compare");
}
