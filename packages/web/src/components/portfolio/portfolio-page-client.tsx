"use client";

import { useQuery } from "@tanstack/react-query";
import type {
  ParetoPoint,
  PortfolioEnvironmentRow,
  PortfolioRow,
} from "@/lib/types";
import { queryKeys } from "@/lib/query-keys";
import { useProjectRevision } from "@/lib/use-project-revision";
import { PageHeader } from "@/components/page-shell";
import { PortfolioPageSkeleton } from "@/components/page-skeletons";
import { PortfolioClient } from "./portfolio-client";

type PortfolioPageClientProps = {
  project: string;
};

type EnvironmentResponseRow = {
  environment: string;
};

type PortfolioResponse = {
  rows: PortfolioRow[];
  environmentRows: PortfolioEnvironmentRow[];
  paretoPoints: ParetoPoint[];
};

/** Cache-first portfolio page shell with page-specific cold-load chrome. */
export function PortfolioPageClient({
  project,
}: PortfolioPageClientProps) {
  useProjectRevision(project, {
    invalidatePrefixes: [
      queryKeys.portfolio.all(project),
      queryKeys.environments.all(project),
    ],
  });

  const portfolioQuery = useQuery({
    queryKey: queryKeys.portfolio.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/portfolio?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch portfolio data");
      }
      const data: PortfolioResponse = await response.json();
      return data;
    },
  });

  const environmentsQuery = useQuery({
    queryKey: queryKeys.environments.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/environments?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch environments");
      }
      const data: EnvironmentResponseRow[] = await response.json();
      return data;
    },
  });

  const showSkeleton =
    (portfolioQuery.data === undefined || environmentsQuery.data === undefined) &&
    (portfolioQuery.isPending || environmentsQuery.isPending);

  const environmentNames =
    environmentsQuery.data?.map((environment) => environment.environment) ?? [];

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PageHeader title="Portfolio Dashboard" />
      {showSkeleton ? (
        <PortfolioPageSkeleton />
      ) : (
        <PortfolioClient
          rows={portfolioQuery.data?.rows ?? []}
          environments={environmentNames}
          environmentRows={portfolioQuery.data?.environmentRows ?? []}
          paretoPoints={portfolioQuery.data?.paretoPoints ?? []}
        />
      )}
    </div>
  );
}
