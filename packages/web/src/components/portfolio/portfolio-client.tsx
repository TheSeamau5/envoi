/**
 * Portfolio dashboard client component.
 * Rankings table with per-environment pass rate bars.
 */

"use client";

import type { PortfolioRow } from "@/lib/types";
import { T } from "@/lib/tokens";

type PortfolioClientProps = {
  rows: PortfolioRow[];
  environments: string[];
};

/** Rank badge colors: gold, silver, bronze, default */
function rankColor(rank: number): string {
  switch (rank) {
    case 1:
      return T.gold;
    case 2:
      return T.textDim;
    case 3:
      return T.accent;
    default:
      return T.textDim;
  }
}

export function PortfolioClient({ rows, environments }: PortfolioClientProps) {
  if (rows.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-envoi-text-dim">
        No portfolio data available
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto">
      {/* Rankings table */}
      <div className="overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr>
              <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted">
                Rank
              </th>
              <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted">
                Model
              </th>
              <th className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-right font-semibold text-envoi-text-muted">
                Avg Rank
              </th>
              {environments.map((env) => (
                <th
                  key={env}
                  className="sticky top-0 border-b border-envoi-border bg-envoi-surface px-[14px] py-[6px] text-left font-semibold text-envoi-text-muted"
                >
                  {env}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr
                key={row.model}
                className={rowIndex % 2 === 0 ? "bg-envoi-bg" : "bg-envoi-surface"}
              >
                <td className="border-b border-envoi-border-light px-[14px] py-[6px]">
                  <span
                    className="font-semibold"
                    style={{ color: rankColor(rowIndex + 1) }}
                  >
                    #{rowIndex + 1}
                  </span>
                </td>
                <td className="border-b border-envoi-border-light px-[14px] py-[6px] font-semibold text-envoi-text">
                  {row.model}
                </td>
                <td className="border-b border-envoi-border-light px-[14px] py-[6px] text-right text-envoi-text-muted">
                  {row.avgRank.toFixed(1)}
                </td>
                {environments.map((env) => {
                  const envData = row.environments[env];
                  const passRate = envData?.passRate ?? 0;
                  return (
                    <td
                      key={env}
                      className="border-b border-envoi-border-light px-[14px] py-[6px]"
                    >
                      <div className="flex items-center gap-[8px]">
                        {/* Pass rate bar */}
                        <div
                          className="h-[6px] rounded-full"
                          style={{
                            width: `${Math.max(2, passRate * 100)}%`,
                            maxWidth: 100,
                            background: T.greenDark,
                          }}
                        />
                        <span className="text-[13px] text-envoi-text-muted">
                          {(passRate * 100).toFixed(0)}%
                        </span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
