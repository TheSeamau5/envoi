"use client";

import { useQuery } from "@tanstack/react-query";
import type { SchemaColumn } from "@/lib/types";
import { queryKeys } from "@/lib/query-keys";
import { useProjectRevision } from "@/lib/use-project-revision";
import { QUERY_TEMPLATES } from "@/lib/query-templates";
import { PageHeader } from "@/components/page-shell";
import { QueryPageSkeleton } from "@/components/page-skeletons";
import { QueryClient } from "./query-client";

type QueryPageClientProps = {
  project: string;
};

/** Cache-first SQL console page shell with a page-specific cold-load skeleton. */
export function QueryPageClient({ project }: QueryPageClientProps) {
  useProjectRevision(project, {
    invalidatePrefixes: [queryKeys.schema.all(project)],
  });

  const schemaQuery = useQuery({
    queryKey: queryKeys.schema.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/schema?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch schema");
      }
      const data: SchemaColumn[] = await response.json();
      return data;
    },
  });

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
