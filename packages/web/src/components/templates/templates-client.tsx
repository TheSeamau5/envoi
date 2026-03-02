/**
 * Query templates client component.
 * Template selector, parameter form, and results with visualization.
 */

"use client";

import { useState, useCallback, useMemo } from "react";
import { Play, Loader2 } from "lucide-react";
import type { QueryTemplate } from "@/lib/types";
import { T } from "@/lib/tokens";

type TemplatesClientProps = {
  templates: QueryTemplate[];
};

type QueryResult = {
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  durationMs: number;
};

/** SVG bar chart layout constants */
const BAR_VIEW_W = 600;
const BAR_VIEW_H = 200;
const BAR_MARGIN = { top: 20, right: 20, bottom: 40, left: 60 };
const BAR_PLOT_W = BAR_VIEW_W - BAR_MARGIN.left - BAR_MARGIN.right;
const BAR_PLOT_H = BAR_VIEW_H - BAR_MARGIN.top - BAR_MARGIN.bottom;

/** SVG line chart layout constants */
const LINE_VIEW_W = 600;
const LINE_VIEW_H = 200;
const LINE_MARGIN = { top: 20, right: 20, bottom: 40, left: 60 };
const LINE_PLOT_W = LINE_VIEW_W - LINE_MARGIN.left - LINE_MARGIN.right;
const LINE_PLOT_H = LINE_VIEW_H - LINE_MARGIN.top - LINE_MARGIN.bottom;

export function TemplatesClient({ templates }: TemplatesClientProps) {
  const [selectedId, setSelectedId] = useState(templates[0]?.id ?? "");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<QueryResult>();
  const [error, setError] = useState<string>();
  const [isLoading, setIsLoading] = useState(false);

  const selected = templates.find((tmpl) => tmpl.id === selectedId);

  /** Initialize param values when template changes */
  const handleSelectTemplate = useCallback((templateId: string) => {
    setSelectedId(templateId);
    setResult(undefined);
    setError(undefined);
    const tmpl = templates.find((template) => template.id === templateId);
    if (tmpl) {
      const defaults: Record<string, string> = {};
      for (const param of tmpl.parameters) {
        defaults[param.name] = param.defaultValue;
      }
      setParamValues(defaults);
    }
  }, [templates]);

  /** Interpolate parameters into SQL */
  const interpolatedSql = useMemo(() => {
    if (!selected) {
      return "";
    }
    let sql = selected.sql;
    for (const param of selected.parameters) {
      const value = paramValues[param.name] ?? param.defaultValue;
      sql = sql.replace(new RegExp(`\\{\\{${param.name}\\}\\}`, "g"), value);
    }
    return sql;
  }, [selected, paramValues]);

  const runTemplate = useCallback(async () => {
    if (interpolatedSql.trim().length === 0) {
      return;
    }

    setIsLoading(true);
    setError(undefined);
    setResult(undefined);

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql: interpolatedSql }),
      });
      const data: unknown = await response.json();

      if (!response.ok) {
        const errorData = data as Record<string, unknown>;
        setError(String(errorData.error ?? "Query failed"));
        return;
      }

      setResult(data as QueryResult);
    } catch {
      setError("Failed to execute query");
    } finally {
      setIsLoading(false);
    }
  }, [interpolatedSql]);

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Template selector (left sidebar) */}
      <div className="flex w-[240px] shrink-0 flex-col overflow-y-auto border-r border-envoi-border bg-envoi-surface">
        <div className="border-b border-envoi-border px-[12px] py-[6px]">
          <span className="text-[9px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Templates
          </span>
        </div>
        {templates.map((tmpl) => (
          <button
            key={tmpl.id}
            onClick={() => handleSelectTemplate(tmpl.id)}
            className={`border-b border-envoi-border-light px-[12px] py-[8px] text-left transition-colors ${
              tmpl.id === selectedId
                ? "border-l-[3px] border-l-envoi-accent bg-envoi-bg"
                : "border-l-[3px] border-l-transparent hover:bg-envoi-border-light"
            }`}
          >
            <div className="text-[10px] font-semibold text-envoi-text">
              {tmpl.name}
            </div>
            <div className="mt-[2px] text-[9px] text-envoi-text-dim">
              {tmpl.description}
            </div>
          </button>
        ))}
      </div>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {selected && (
          <>
            {/* Parameters + run */}
            <div className="flex items-end gap-[10px] border-b border-envoi-border px-[14px] py-[10px]">
              {selected.parameters.map((param) => (
                <div key={param.name} className="flex flex-col gap-[4px]">
                  <label className="text-[9px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                    {param.label}
                  </label>
                  <input
                    type="text"
                    value={paramValues[param.name] ?? param.defaultValue}
                    onChange={(event) =>
                      setParamValues((prev) => ({
                        ...prev,
                        [param.name]: event.target.value,
                      }))
                    }
                    className="rounded border border-envoi-border-light bg-envoi-bg px-[8px] py-[4px] text-[10px] text-envoi-text outline-none focus:border-envoi-accent"
                    style={{ fontFamily: T.mono, width: 200 }}
                  />
                </div>
              ))}

              <button
                onClick={runTemplate}
                disabled={isLoading}
                className="flex items-center gap-[6px] rounded bg-envoi-accent px-[12px] py-[5px] text-[10px] font-semibold text-white transition-opacity disabled:opacity-40"
              >
                {isLoading ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Play size={12} />
                )}
                Run
              </button>
            </div>

            {/* SQL preview */}
            <div className="border-b border-envoi-border-light bg-envoi-surface px-[14px] py-[6px]">
              <pre className="text-[9px] leading-[14px] text-envoi-text-muted" style={{ fontFamily: T.mono }}>
                {interpolatedSql}
              </pre>
            </div>

            {/* Results */}
            <div className="flex-1 overflow-auto">
              {error && (
                <div
                  className="border-b px-[14px] py-[10px] text-[11px]"
                  style={{ background: T.redBgOpaque, color: T.redDark, borderColor: T.redBorderLight }}
                >
                  {error}
                </div>
              )}

              {result && (
                <div className="px-[14px] py-[14px]">
                  {selected.visualization === "bar" && result.columns.length >= 2 && (
                    <BarChart result={result} />
                  )}
                  {selected.visualization === "line" && result.columns.length >= 2 && (
                    <LineChart result={result} />
                  )}
                  <ResultTable result={result} />
                </div>
              )}

              {!result && !error && !isLoading && (
                <div className="flex items-center justify-center py-[40px] text-[11px] text-envoi-text-dim">
                  Click Run to execute this template
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Simple SVG bar chart — first column as label, second numeric column as value */
function BarChart({ result }: { result: QueryResult }) {
  const labelCol = result.columns[0];
  const valueCol = result.columns.length > 2 ? result.columns[2] : result.columns[1];
  if (!labelCol || !valueCol) {
    return undefined;
  }

  const data = result.rows.map((row) => ({
    label: String(row[labelCol] ?? ""),
    value: Number(row[valueCol] ?? 0),
  }));

  const maxValue = Math.max(...data.map((point) => point.value), 0.01);
  const barWidth = Math.max(8, Math.min(40, BAR_PLOT_W / data.length - 4));

  return (
    <div className="mb-[14px]">
      <svg viewBox={`0 0 ${BAR_VIEW_W} ${BAR_VIEW_H}`} className="w-full" style={{ maxWidth: BAR_VIEW_W }}>
        {/* Y axis label */}
        <text
          x={BAR_MARGIN.left - 8}
          y={BAR_MARGIN.top}
          textAnchor="end"
          style={{ fontSize: "8px", fill: T.textDim }}
        >
          {maxValue <= 1 ? "100%" : maxValue.toFixed(0)}
        </text>
        <text
          x={BAR_MARGIN.left - 8}
          y={BAR_MARGIN.top + BAR_PLOT_H}
          textAnchor="end"
          style={{ fontSize: "8px", fill: T.textDim }}
        >
          0
        </text>

        {/* Bars */}
        {data.map((point, barIndex) => {
          const barX = BAR_MARGIN.left + (barIndex / data.length) * BAR_PLOT_W + (BAR_PLOT_W / data.length - barWidth) / 2;
          const barHeight = (point.value / maxValue) * BAR_PLOT_H;
          const barY = BAR_MARGIN.top + BAR_PLOT_H - barHeight;

          return (
            <g key={barIndex}>
              <rect
                x={barX}
                y={barY}
                width={barWidth}
                height={barHeight}
                fill={T.accent}
                rx={2}
              />
              {/* Label */}
              <text
                x={barX + barWidth / 2}
                y={BAR_MARGIN.top + BAR_PLOT_H + 14}
                textAnchor="middle"
                style={{ fontSize: "7px", fill: T.textDim }}
              >
                {point.label.length > 10 ? `${point.label.slice(0, 10)}...` : point.label}
              </text>
            </g>
          );
        })}

        {/* Baseline */}
        <line
          x1={BAR_MARGIN.left}
          y1={BAR_MARGIN.top + BAR_PLOT_H}
          x2={BAR_MARGIN.left + BAR_PLOT_W}
          y2={BAR_MARGIN.top + BAR_PLOT_H}
          stroke={T.borderLight}
          strokeWidth={1}
        />
      </svg>
    </div>
  );
}

/** Simple SVG line chart — first column as X, second numeric column as Y */
function LineChart({ result }: { result: QueryResult }) {
  const xCol = result.columns[0];
  const yCol = result.columns[1];
  if (!xCol || !yCol) {
    return undefined;
  }

  const data = result.rows.map((row) => ({
    x: Number(row[xCol] ?? 0),
    y: Number(row[yCol] ?? 0),
  }));

  if (data.length < 2) {
    return undefined;
  }

  const xMin = Math.min(...data.map((point) => point.x));
  const xMax = Math.max(...data.map((point) => point.x));
  const yMax = Math.max(...data.map((point) => point.y), 0.01);
  const xRange = xMax - xMin || 1;

  const toX = (value: number) => LINE_MARGIN.left + ((value - xMin) / xRange) * LINE_PLOT_W;
  const toY = (value: number) => LINE_MARGIN.top + LINE_PLOT_H - (value / yMax) * LINE_PLOT_H;

  const pathD = data
    .map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"}${toX(point.x).toFixed(1)},${toY(point.y).toFixed(1)}`)
    .join(" ");

  return (
    <div className="mb-[14px]">
      <svg viewBox={`0 0 ${LINE_VIEW_W} ${LINE_VIEW_H}`} className="w-full" style={{ maxWidth: LINE_VIEW_W }}>
        {/* Grid */}
        <line
          x1={LINE_MARGIN.left}
          y1={LINE_MARGIN.top + LINE_PLOT_H}
          x2={LINE_MARGIN.left + LINE_PLOT_W}
          y2={LINE_MARGIN.top + LINE_PLOT_H}
          stroke={T.borderLight}
          strokeWidth={1}
        />

        {/* Y axis labels */}
        <text x={LINE_MARGIN.left - 8} y={LINE_MARGIN.top + 3} textAnchor="end" style={{ fontSize: "8px", fill: T.textDim }}>
          {yMax.toFixed(0)}
        </text>
        <text x={LINE_MARGIN.left - 8} y={LINE_MARGIN.top + LINE_PLOT_H + 3} textAnchor="end" style={{ fontSize: "8px", fill: T.textDim }}>
          0
        </text>

        {/* Line */}
        <path d={pathD} fill="none" stroke={T.accent} strokeWidth={1.5} />

        {/* Dots */}
        {data.map((point, dotIndex) => (
          <circle key={dotIndex} cx={toX(point.x)} cy={toY(point.y)} r={2} fill={T.accent} />
        ))}
      </svg>
    </div>
  );
}

/** Results table */
function ResultTable({ result }: { result: QueryResult }) {
  return (
    <div>
      <div className="mb-[6px] text-[9px] text-envoi-text-dim">
        {result.rowCount} rows · {result.durationMs}ms
      </div>
      <div className="overflow-auto rounded border border-envoi-border">
        <table className="w-full border-collapse text-[10px]">
          <thead>
            <tr>
              {result.columns.map((col) => (
                <th
                  key={col}
                  className="border-b border-envoi-border bg-envoi-surface px-[10px] py-[4px] text-left font-semibold text-envoi-text-muted"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className={rowIndex % 2 === 0 ? "bg-envoi-bg" : "bg-envoi-surface"}>
                {result.columns.map((col) => (
                  <td
                    key={col}
                    className="border-b border-envoi-border-light px-[10px] py-[3px] text-envoi-text"
                    style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {formatCellValue(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Format a cell value for display */
function formatCellValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "NULL";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
