/**
 * Query Templates page — server component.
 * Shows predefined query templates with parameter inputs and visualizations.
 */

import { QUERY_TEMPLATES } from "@/lib/query-templates";
import { TemplatesClient } from "@/components/templates/templates-client";

export default function TemplatesPage() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Query Templates
        </span>
      </div>
      <TemplatesClient templates={QUERY_TEMPLATES} />
    </div>
  );
}
