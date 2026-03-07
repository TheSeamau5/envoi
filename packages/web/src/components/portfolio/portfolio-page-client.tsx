"use client";

import type {
  ParetoPoint,
  PortfolioEnvironmentRow,
  PortfolioRow,
} from "@/lib/types";
import { useProjectPortfolio } from "@/lib/project-data";
import { PageHeader } from "@/components/page-shell";
import { PortfolioPageSkeleton } from "@/components/page-skeletons";
import { PortfolioClient } from "./portfolio-client";

type PortfolioPageClientProps = {
  project: string;
  initialData?: PortfolioResponse;
};

type PortfolioResponse = {
  rows: PortfolioRow[];
  environmentRows: PortfolioEnvironmentRow[];
  paretoPoints: ParetoPoint[];
};

/** Cache-first portfolio page shell with page-specific cold-load chrome. */
export function PortfolioPageClient({
  project,
  initialData,
}: PortfolioPageClientProps) {
  const portfolioQuery = useProjectPortfolio(project, initialData);

  const showSkeleton =
    portfolioQuery.data === undefined && portfolioQuery.isPending;

  const environmentNames = portfolioQuery.data?.environmentRows.map(
    (environment) => environment.environment,
  ) ?? [];

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
