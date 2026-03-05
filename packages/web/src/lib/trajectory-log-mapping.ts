import type {
  Commit,
  ResolvedTrajectoryLog,
  TrajectoryLogMatchKind,
  TrajectoryLogRow,
} from "@/lib/types";

type CommitWindow = {
  position: number;
  turn: number;
  hash: string;
  partStart: number;
  partEnd: number;
  timestampMs?: number;
};

type ResolvedWindow = {
  commitIndex: number;
  commitHash: string;
  turn: number;
  partEnd: number;
};

function parseIsoToMs(value: string | undefined): number | undefined {
  if (!value) {
    return undefined;
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return undefined;
  }
  return parsed;
}

function toCommitWindows(commits: Commit[]): CommitWindow[] {
  const windows: CommitWindow[] = [];
  for (let position = 0; position < commits.length; position++) {
    const commit = commits[position];
    if (!commit) {
      continue;
    }
    const range = commit.partRange;
    if (!range) {
      continue;
    }
    const [partStart, partEnd] = range;
    if (
      typeof partStart !== "number" ||
      typeof partEnd !== "number" ||
      partEnd < partStart
    ) {
      continue;
    }
    windows.push({
      position,
      turn: commit.turn,
      hash: commit.hash,
      partStart,
      partEnd,
      timestampMs: parseIsoToMs(commit.timestamp),
    });
  }
  return windows;
}

function findCommitByPart(
  windows: CommitWindow[],
  part: number,
): CommitWindow | undefined {
  for (const window of windows) {
    if (part >= window.partStart && part <= window.partEnd) {
      return window;
    }
  }
  return undefined;
}

function findCommitByHash(
  windows: CommitWindow[],
  candidateHash: string,
): CommitWindow | undefined {
  const normalized = candidateHash.trim().toLowerCase();
  if (!normalized) {
    return undefined;
  }

  const exact = windows.find(
    (window) => window.hash.toLowerCase() === normalized,
  );
  if (exact) {
    return exact;
  }

  // Eval logs often carry a short hash (e.g. 10 chars). Resolve unique prefix.
  const prefixMatches = windows.filter((window) =>
    window.hash.toLowerCase().startsWith(normalized),
  );
  if (prefixMatches.length === 1) {
    return prefixMatches[0];
  }
  return undefined;
}

function findCommitByTimestamp(
  windows: CommitWindow[],
  tsIso: string | undefined,
): CommitWindow | undefined {
  const tsMs = parseIsoToMs(tsIso);
  if (tsMs === undefined) {
    return undefined;
  }
  let candidate: CommitWindow | undefined;
  for (const window of windows) {
    const commitTs = window.timestampMs;
    if (commitTs === undefined) {
      continue;
    }
    if (commitTs <= tsMs) {
      candidate = window;
    } else {
      break;
    }
  }
  return candidate ?? windows[0];
}

function findCommitByTurn(
  windows: CommitWindow[],
  turn: number | undefined,
  tsIso: string | undefined,
): CommitWindow | undefined {
  if (turn === undefined) {
    return undefined;
  }
  const turnWindows = windows.filter((window) => window.turn === turn);
  if (turnWindows.length === 0) {
    return undefined;
  }
  const byTime = findCommitByTimestamp(turnWindows, tsIso);
  if (byTime) {
    return byTime;
  }
  return turnWindows[0];
}

const messagePartPatterns = [
  /\bfrom\s+part\s+(\d+)\b/i,
  /\bpart\s*=\s*(\d+)\b/i,
  /\bpart\s+(\d+)\b/i,
];

function parseMessagePart(message: string): number | undefined {
  for (const pattern of messagePartPatterns) {
    const match = pattern.exec(message);
    if (!match) {
      continue;
    }
    const value = Number(match[1]);
    if (Number.isInteger(value) && value >= 1) {
      return value;
    }
  }
  return undefined;
}

const messageCommitPatterns = [
  /\bcommit=([0-9a-f]{7,40})\b/i,
  /\bcommit\s+([0-9a-f]{7,40})\b/i,
  /\bcommitted(?:\s+part\s+\d+)?\s*:\s*([0-9a-f]{7,40})\b/i,
];

function parseMessageCommitCandidates(message: string): string[] {
  const candidates: string[] = [];
  for (const pattern of messageCommitPatterns) {
    const match = pattern.exec(message);
    const hash = match?.[1];
    if (!hash) {
      continue;
    }
    const normalized = hash.toLowerCase();
    if (!candidates.includes(normalized)) {
      candidates.push(normalized);
    }
  }
  return candidates;
}

function parseMessagePrefix(message: string): string {
  const match = /^\[([^\]]+)\]/.exec(message.trim());
  const prefix = match?.[1]?.trim().toLowerCase();
  return prefix && prefix.length > 0 ? prefix : "log";
}

function resolveFromWindow(window: CommitWindow): ResolvedWindow {
  return {
    commitIndex: window.position,
    commitHash: window.hash,
    turn: window.turn,
    partEnd: window.partEnd,
  };
}

export function resolveTrajectoryLogs(
  logs: TrajectoryLogRow[],
  commits: Commit[],
): ResolvedTrajectoryLog[] {
  if (logs.length === 0) {
    return [];
  }

  const windows = toCommitWindows(commits);
  const firstWindow = windows[0];
  const firstTurn = firstWindow?.turn ?? commits[0]?.turn ?? 1;
  const firstPart = firstWindow?.partStart ?? 1;
  const firstCommitIndex = firstWindow?.position ?? 0;
  const firstCommitHash = firstWindow?.hash ?? commits[0]?.hash ?? "unknown";

  return logs.map((row) => {
    const prefix = parseMessagePrefix(row.message);
    let resolvedTurn = row.turn;
    let resolvedPart = row.part;
    let resolvedWindow: ResolvedWindow | undefined;
    let matchKind: TrajectoryLogMatchKind = "unmapped";
    let inferred = false;

    if (row.turn === undefined && row.part === undefined) {
      resolvedTurn = firstTurn;
      resolvedPart = firstPart;
      matchKind = "synthetic_first_context";
      inferred = true;
      resolvedWindow = {
        commitIndex: firstCommitIndex,
        commitHash: firstCommitHash,
        turn: firstTurn,
        partEnd: firstPart,
      };
    } else {
      if (typeof row.part === "number" && row.part >= 1) {
        const byPart = findCommitByPart(windows, row.part);
        if (byPart) {
          resolvedWindow = resolveFromWindow(byPart);
          matchKind = "part";
        }
      }

      if (!resolvedWindow) {
        const messagePart = parseMessagePart(row.message);
        if (messagePart !== undefined) {
          const byMessagePart = findCommitByPart(windows, messagePart);
          if (byMessagePart) {
            resolvedPart = messagePart;
            resolvedWindow = resolveFromWindow(byMessagePart);
            matchKind = "message_part";
            inferred = true;
          }
        }
      }

      if (!resolvedWindow) {
        const messageCommitCandidates = parseMessageCommitCandidates(
          row.message,
        );
        for (const candidate of messageCommitCandidates) {
          const byMessageCommit = findCommitByHash(windows, candidate);
          if (!byMessageCommit) {
            continue;
          }
          resolvedWindow = resolveFromWindow(byMessageCommit);
          matchKind = "message_commit";
          inferred = true;
          if (resolvedPart === undefined || resolvedPart <= 0) {
            resolvedPart = byMessageCommit.partEnd;
          }
          break;
        }
      }

      if (!resolvedWindow && row.gitCommit) {
        const byGit = findCommitByHash(windows, row.gitCommit);
        if (byGit) {
          resolvedWindow = resolveFromWindow(byGit);
          matchKind = "git_commit";
        }
      }

      if (!resolvedWindow) {
        const byTimestamp = findCommitByTimestamp(windows, row.ts);
        if (byTimestamp) {
          resolvedWindow = resolveFromWindow(byTimestamp);
          matchKind = "timestamp";
          inferred = true;
        }
      }

      if (!resolvedWindow) {
        const byTurn = findCommitByTurn(windows, row.turn, row.ts);
        if (byTurn) {
          resolvedWindow = resolveFromWindow(byTurn);
          matchKind = "turn";
          inferred = true;
        }
      }
    }

    if (resolvedWindow && resolvedTurn === undefined) {
      resolvedTurn = resolvedWindow.turn;
    }
    if (
      resolvedWindow &&
      (resolvedPart === undefined || resolvedPart <= 0) &&
      matchKind !== "unmapped"
    ) {
      resolvedPart = resolvedWindow.partEnd;
    }

    return {
      ...row,
      prefix,
      resolvedTurn,
      resolvedPart,
      resolvedCommitIndex: resolvedWindow?.commitIndex,
      resolvedCommitHash: resolvedWindow?.commitHash,
      matchKind,
      inferred,
    };
  });
}
