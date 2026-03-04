/**
 * Code panel — file tree (190px left) + code viewer (right).
 * Client component — manages selected file state.
 * Auto-selects first changed file when commit changes.
 *
 * ZERO useEffect — file selection is derived via useMemo.
 * Manual selection is tracked in state; when commit changes
 * and the selected file no longer exists, we auto-select.
 */

"use client";

import { useState, useMemo } from "react";
import type { Commit } from "@/lib/types";
import { FileTree } from "./file-tree";
import { CodeView } from "./code-view";

type CodePanelProps = {
  commit: Commit;
};

export function CodePanel({ commit }: CodePanelProps) {
  const [manualSelection, setManualSelection] = useState<string | undefined>(
    undefined,
  );

  /**
   * Derive the effective selected file:
   * - If user manually selected a file and it exists in the snapshot, use it.
   * - Otherwise auto-select first changed file, then first snapshot file.
   */
  const selectedFile = useMemo(() => {
    if (manualSelection && commit.codeSnapshot[manualSelection]) {
      return manualSelection;
    }
    const firstChanged = commit.changedFiles[0];
    if (firstChanged) {
      return firstChanged.path;
    }
    const allFiles = Object.keys(commit.codeSnapshot);
    return allFiles[0];
  }, [manualSelection, commit.codeSnapshot, commit.changedFiles]);

  const fileSnapshot = selectedFile
    ? commit.codeSnapshot[selectedFile]
    : undefined;

  const changedFile = selectedFile
    ? commit.changedFiles.find((file) => file.path === selectedFile)
    : undefined;

  return (
    <div className="flex flex-1 overflow-hidden">
      <FileTree
        snapshot={commit.codeSnapshot}
        selectedFile={selectedFile}
        onSelectFile={setManualSelection}
      />
      <CodeView
        snapshot={fileSnapshot}
        filePath={selectedFile}
        additions={changedFile?.additions}
        deletions={changedFile?.deletions}
      />
    </div>
  );
}
