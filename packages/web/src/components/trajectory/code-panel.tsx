/**
 * Code panel — file tree (190px left) + code viewer (right).
 * Client component — manages selected file state.
 * Auto-selects first changed file on commit change.
 */

"use client";

import { useState, useEffect } from "react";
import type { Commit } from "@/lib/types";
import { FileTree } from "./file-tree";
import { CodeView } from "./code-view";

type CodePanelProps = {
  commit: Commit;
};

export function CodePanel({ commit }: CodePanelProps) {
  const [selectedFile, setSelectedFile] = useState<string>();

  /** Auto-select file when commit changes.
   *  If the currently viewed file exists in the new commit, stay on it.
   *  Otherwise fall back to first changed file, then first snapshot file. */
  useEffect(() => {
    if (selectedFile && commit.codeSnapshot[selectedFile]) {
      return;
    }
    const firstChanged = commit.changedFiles[0];
    if (firstChanged) {
      setSelectedFile(firstChanged.path);
    } else {
      /** Fallback to first file in snapshot */
      const allFiles = Object.keys(commit.codeSnapshot);
      setSelectedFile(allFiles[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commit.index, commit.changedFiles, commit.codeSnapshot]);

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
        onSelectFile={setSelectedFile}
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
