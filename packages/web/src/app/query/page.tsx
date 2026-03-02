/**
 * SQL Console page — server component.
 * Fetches schema info, provides built-in templates, passes to client.
 */

import { getSchemaInfo } from "@/lib/server/data";
import { QUERY_TEMPLATES } from "@/lib/query-templates";
import { QueryClient } from "@/components/query/query-client";

export default async function QueryPage() {
  const schema = await getSchemaInfo();

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          SQL Console
        </span>
      </div>
      <QueryClient schema={schema} builtinTemplates={QUERY_TEMPLATES} />
    </div>
  );
}
