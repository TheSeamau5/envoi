"use client";

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query-keys";

const REVISION_POLL_MS = 5_000;

export type ProjectRevisionStatus = {
  s3Revision?: string;
  loadedRevision?: string;
  inSync: boolean;
  hasManifest: boolean;
  lastCheckedAt?: string;
  lastLoadedAt?: string;
  revisionLagMs: number;
  refreshDurationMs?: number;
  publishedAt?: string;
};

type UseProjectRevisionOptions = {
  enabled?: boolean;
  invalidatePrefixes?: readonly ReadonlyArray<unknown>[];
};

/** Poll the server revision endpoint and invalidate queries when a new revision lands. */
export function useProjectRevision(
  project: string,
  options?: UseProjectRevisionOptions,
): ProjectRevisionStatus | undefined {
  const enabled = options?.enabled !== false;
  const queryClient = useQueryClient();
  const previousRevisionRef = useRef<string | undefined>(undefined);
  const hasPrimedInvalidationRef = useRef(false);

  const revisionQuery = useQuery({
    queryKey: queryKeys.revision.status(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/revision?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch project revision");
      }
      const data: ProjectRevisionStatus = await response.json();
      return data;
    },
    enabled,
    refetchInterval: enabled ? REVISION_POLL_MS : false,
    staleTime: 0,
  });

  useEffect(() => {
    const loadedRevision = revisionQuery.data?.loadedRevision;
    if (!loadedRevision) {
      return;
    }
    if (previousRevisionRef.current === undefined) {
      previousRevisionRef.current = loadedRevision;
      if (hasPrimedInvalidationRef.current) {
        return;
      }
      hasPrimedInvalidationRef.current = true;
      for (const prefix of options?.invalidatePrefixes ?? []) {
        void queryClient.invalidateQueries({ queryKey: [...prefix] });
      }
      return;
    }
    if (previousRevisionRef.current === loadedRevision) {
      return;
    }
    previousRevisionRef.current = loadedRevision;
    for (const prefix of options?.invalidatePrefixes ?? []) {
      void queryClient.invalidateQueries({ queryKey: [...prefix] });
    }
  }, [options?.invalidatePrefixes, queryClient, revisionQuery.data?.loadedRevision]);

  return revisionQuery.data;
}
