"use client";

import type { SchemaColumn } from "@/lib/types";
import { useProjectSchema } from "@/lib/project-data";
import { QUERY_TEMPLATES } from "@/lib/query-templates";
import { PageHeader } from "@/components/page-shell";
import { QueryPageSkeleton } from "@/components/page-skeletons";
import { QueryClient } from "./query-client";

type QueryPageClientProps = {
  project: string;
  initialSchema?: SchemaColumn[];
};

/** Cache-first SQL console page shell with a page-specific cold-load skeleton. */
export function QueryPageClient({
  project,
  initialSchema,
}: QueryPageClientProps) {
  const schemaQuery = useProjectSchema(project, initialSchema);

  const schema = schemaQuery.data;
  const showSkeleton = schema === undefined && schemaQuery.isPending;

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PageHeader title="SQL Console" />
      {showSkeleton ? (
        <QueryPageSkeleton />
      ) : (
        <QueryClient
          schema={schema ?? []}
          builtinTemplates={QUERY_TEMPLATES}
          project={project}
        />
      )}
    </div>
  );
}
