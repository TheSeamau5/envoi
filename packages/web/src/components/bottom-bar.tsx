/**
 * Bottom status bar — server-renderable.
 * Shows contextual info on the left and system status on the right.
 */

import { TOTAL_TESTS, SUITES } from "@/lib/constants";
import { Circle } from "lucide-react";

export function BottomBar() {
  return (
    <div className="flex h-[30px] shrink-0 items-center border-t border-envoi-border bg-envoi-bg px-5 text-[10px] text-envoi-text-dim">
      <span>
        {TOTAL_TESTS} tests &middot; {SUITES.length} suites
      </span>
      <div className="flex-1" />
      <span className="flex items-center gap-[5px]">
        <Circle size={6} fill="#10b981" className="text-envoi-green" />
        ALL SYSTEMS OPERATIONAL
      </span>
    </div>
  );
}
