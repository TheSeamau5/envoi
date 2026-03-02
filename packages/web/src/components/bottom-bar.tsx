/**
 * Bottom status bar — server-renderable.
 * Shows contextual info on the left and system status on the right.
 */

import { TOTAL_TESTS as DEFAULT_TOTAL_TESTS, SUITES as DEFAULT_SUITES } from "@/lib/constants";
import type { Suite } from "@/lib/types";
import { Circle } from "lucide-react";
import { T } from "@/lib/tokens";

type BottomBarProps = {
  suites?: Suite[];
  totalTests?: number;
};

export function BottomBar({ suites, totalTests }: BottomBarProps) {
  const effectiveSuites = suites ?? DEFAULT_SUITES;
  const effectiveTotal = totalTests ?? DEFAULT_TOTAL_TESTS;
  return (
    <div className="flex h-7.5 shrink-0 items-center border-t border-envoi-border bg-envoi-bg px-5 text-[10px] text-envoi-text-dim">
      <span>
        {effectiveTotal} tests &middot; {effectiveSuites.length} suites
      </span>
      <div className="flex-1" />
      <span className="flex items-center gap-1.25">
        <Circle size={6} fill={T.green} className="text-envoi-green" />
        ALL SYSTEMS OPERATIONAL
      </span>
    </div>
  );
}
