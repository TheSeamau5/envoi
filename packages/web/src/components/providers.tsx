/**
 * Client-side providers wrapper.
 * Wraps children with TooltipProvider so tooltips work anywhere in the tree.
 */

"use client";

import type { ReactNode } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";

type ProvidersProps = {
  children: ReactNode;
};

export function Providers({ children }: ProvidersProps) {
  console.log("[DEBUG] Providers render");
  return <TooltipProvider delayDuration={0}>{children}</TooltipProvider>;
}
