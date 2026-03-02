/**
 * Portfolio Dashboard page — server component.
 * Shows cross-environment model rankings, environment summaries, and Pareto frontier.
 */

import {
  getPortfolioData,
  getEnvironments,
  getPortfolioEnvironmentData,
  getParetoData,
} from "@/lib/server/data";
import { PortfolioClient } from "@/components/portfolio/portfolio-client";

export default async function PortfolioPage() {
  const [portfolioRows, environments, environmentRows, paretoPoints] = await Promise.all([
    getPortfolioData(),
    getEnvironments(),
    getPortfolioEnvironmentData(),
    getParetoData(),
  ]);

  const environmentNames = environments.map((env) => env.environment);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Portfolio Dashboard
        </span>
      </div>
      <PortfolioClient
        rows={portfolioRows}
        environments={environmentNames}
        environmentRows={environmentRows}
        paretoPoints={paretoPoints}
      />
    </div>
  );
}
