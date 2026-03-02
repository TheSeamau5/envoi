/**
 * Predefined query templates for the Templates page.
 * Each template has a parameterized SQL query and visualization type.
 *
 * Available views (always present when parquet data exists):
 *   - trajectories: per-trajectory summary (id, environment, agent_model, total_parts, total_turns, total_tokens)
 *   - evaluations: per-evaluation event (passed, failed, total, suite_results)
 *   - turn_summaries: per-turn aggregation (tokens, duration, tool calls)
 *   - file_access: per-file-operation (Read/Write/Edit with file_path, tokens)
 */

import type { QueryTemplate } from "@/lib/types";

/** All available query templates */
export const QUERY_TEMPLATES: QueryTemplate[] = [
  {
    id: "model-comparison",
    name: "Model Comparison",
    description: "Compare best pass rates across models",
    sql: `WITH best_eval AS (
  SELECT
    trajectory_id,
    MAX(passed) AS best_passed,
    MAX(total) AS best_total
  FROM evaluations
  WHERE status = 'completed'
  GROUP BY trajectory_id
),
traj AS (
  SELECT
    t.trajectory_id,
    t.agent_model,
    t.environment,
    b.best_passed,
    b.best_total
  FROM trajectories t
  JOIN best_eval b ON b.trajectory_id = t.trajectory_id
)
SELECT
  agent_model AS model,
  COUNT(*) AS trajectories,
  AVG(best_passed * 1.0 / NULLIF(best_total, 0)) AS avg_pass_rate
FROM traj
WHERE environment = '{{environment}}'
GROUP BY agent_model
ORDER BY avg_pass_rate DESC`,
    parameters: [
      {
        name: "environment",
        label: "Environment",
        type: "text",
        defaultValue: "c_compiler",
      },
    ],
    visualization: "bar",
  },
  {
    id: "token-efficiency",
    name: "Token Efficiency",
    description: "Tokens consumed per test passed, grouped by model",
    sql: `WITH best_eval AS (
  SELECT
    trajectory_id,
    MAX(passed) AS best_passed
  FROM evaluations
  WHERE status = 'completed'
  GROUP BY trajectory_id
)
SELECT
  t.agent_model AS model,
  AVG(t.total_tokens) AS avg_tokens,
  AVG(b.best_passed) AS avg_passed,
  CASE
    WHEN AVG(b.best_passed) > 0
    THEN AVG(t.total_tokens) / AVG(b.best_passed)
    ELSE 0
  END AS tokens_per_test
FROM trajectories t
JOIN best_eval b ON b.trajectory_id = t.trajectory_id
GROUP BY t.agent_model
ORDER BY tokens_per_test ASC`,
    parameters: [],
    visualization: "table",
  },
  {
    id: "model-trajectory-count",
    name: "Trajectories per Model",
    description: "Number of trajectories and total tokens by model",
    sql: `SELECT
  agent_model AS model,
  COUNT(*) AS trajectories,
  SUM(total_tokens) AS total_tokens,
  AVG(total_parts) AS avg_parts
FROM trajectories
GROUP BY agent_model
ORDER BY trajectories DESC`,
    parameters: [],
    visualization: "bar",
  },
  {
    id: "turn-duration",
    name: "Turn Duration",
    description: "Average turn duration over time for all trajectories",
    sql: `SELECT
  turn,
  AVG(total_duration_ms) AS avg_duration_ms,
  AVG(num_parts) AS avg_parts,
  AVG(total_content_tokens) AS avg_tokens
FROM turn_summaries
GROUP BY turn
ORDER BY turn
LIMIT 100`,
    parameters: [],
    visualization: "line",
  },
  {
    id: "file-hotspots",
    name: "File Hotspots",
    description: "Most frequently accessed files across all trajectories",
    sql: `SELECT
  file_path,
  COUNT(*) AS access_count,
  COUNT(DISTINCT trajectory_id) AS trajectory_count,
  SUM(tokens) AS total_tokens
FROM file_access
WHERE file_path IS NOT NULL
GROUP BY file_path
ORDER BY access_count DESC
LIMIT 50`,
    parameters: [],
    visualization: "table",
  },
  {
    id: "pareto-candidates",
    name: "Pareto Frontier Candidates",
    description: "Trajectories ranked by cost vs score — candidates for the efficiency frontier",
    sql: `WITH scores AS (
  SELECT
    t.trajectory_id,
    t.agent_model,
    t.environment,
    t.total_tokens,
    MAX(e.passed) AS best_passed,
    MAX(e.total) AS best_total
  FROM trajectories t
  JOIN evaluations e ON e.trajectory_id = t.trajectory_id
  WHERE e.status = 'completed'
  GROUP BY t.trajectory_id, t.agent_model, t.environment, t.total_tokens
)
SELECT
  trajectory_id,
  agent_model,
  environment,
  total_tokens,
  best_passed,
  best_total,
  ROUND(best_passed * 1.0 / NULLIF(best_total, 0), 4) AS pass_rate
FROM scores
WHERE environment = '{{environment}}'
ORDER BY total_tokens ASC, pass_rate DESC`,
    parameters: [
      {
        name: "environment",
        label: "Environment",
        type: "text",
        defaultValue: "c_compiler",
      },
    ],
    visualization: "table",
  },
  {
    id: "regression-detection",
    name: "Regression Detection",
    description: "Evaluation points where test count dropped — regressions across all trajectories",
    sql: `WITH scored AS (
  SELECT
    trajectory_id,
    part,
    passed,
    LAG(passed) OVER (PARTITION BY trajectory_id ORDER BY part) AS prev_passed
  FROM evaluations
  WHERE status = 'completed'
)
SELECT
  trajectory_id,
  part,
  prev_passed,
  passed,
  prev_passed - passed AS tests_regressed
FROM scored
WHERE passed < prev_passed
ORDER BY tests_regressed DESC
LIMIT 50`,
    parameters: [],
    visualization: "table",
  },
  {
    id: "redundant-reads",
    name: "Redundant File Reads",
    description: "Files read multiple times in a row without intervening writes — wasted tokens",
    sql: `WITH consecutive AS (
  SELECT
    trajectory_id,
    file_path,
    tool_name,
    LAG(tool_name) OVER (
      PARTITION BY trajectory_id, file_path ORDER BY part
    ) AS prev_tool
  FROM file_access
  WHERE file_path IS NOT NULL
)
SELECT
  trajectory_id,
  file_path,
  COUNT(*) AS redundant_reads
FROM consecutive
WHERE tool_name IN ('Read', 'file_read')
  AND prev_tool IN ('Read', 'file_read')
GROUP BY trajectory_id, file_path
HAVING COUNT(*) > 1
ORDER BY redundant_reads DESC
LIMIT 50`,
    parameters: [],
    visualization: "table",
  },
  {
    id: "frontier-trajectories",
    name: "Frontier Trajectories (35-65%)",
    description: "Trajectories near 50% pass rate — optimal difficulty for training signal",
    sql: `WITH scores AS (
  SELECT
    t.trajectory_id,
    t.environment,
    t.agent_model,
    t.total_tokens,
    MAX(e.passed) AS max_passed,
    MAX(e.total) AS max_total
  FROM trajectories t
  JOIN evaluations e ON e.trajectory_id = t.trajectory_id
  WHERE e.status = 'completed'
  GROUP BY t.trajectory_id, t.environment, t.agent_model, t.total_tokens
)
SELECT
  trajectory_id,
  environment,
  agent_model,
  max_passed,
  max_total,
  ROUND(max_passed * 1.0 / NULLIF(max_total, 0), 3) AS pass_rate,
  total_tokens
FROM scores
WHERE max_passed * 1.0 / NULLIF(max_total, 0) BETWEEN 0.35 AND 0.65
ORDER BY environment, pass_rate`,
    parameters: [],
    visualization: "table",
  },
  {
    id: "tokens-per-test",
    name: "Token Efficiency per Test",
    description: "Tokens spent per test passed — lower is more efficient",
    sql: `SELECT
  t.trajectory_id,
  t.environment,
  t.agent_model,
  t.total_tokens,
  MAX(e.passed) AS max_passed,
  CASE
    WHEN MAX(e.passed) > 0
    THEN ROUND(t.total_tokens * 1.0 / MAX(e.passed), 0)
    ELSE NULL
  END AS tokens_per_test
FROM trajectories t
JOIN evaluations e ON e.trajectory_id = t.trajectory_id
WHERE e.status = 'completed'
GROUP BY t.trajectory_id, t.environment, t.agent_model, t.total_tokens
HAVING MAX(e.passed) > 0
ORDER BY tokens_per_test ASC
LIMIT 50`,
    parameters: [],
    visualization: "table",
  },
];
