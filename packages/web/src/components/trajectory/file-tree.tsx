/**
 * Expandable file tree for the code panel.
 * Client component — manages folder expand/collapse state.
 *
 * Icons:
 * - FileCode2 for .rs files
 * - FileJson for .json/.toml files
 * - Terminal for .sh files
 * - Folder/FolderOpen for directories
 * - Circle (orange) for modified indicator
 */

"use client";

import { useState, useMemo } from "react";
import {
  FileCode2,
  FileJson,
  Terminal,
  Folder,
  FolderOpen,
  Circle,
} from "lucide-react";
import type { CodeSnapshot } from "@/lib/types";
import { T } from "@/lib/tokens";

type FileTreeProps = {
  snapshot: CodeSnapshot;
  selectedFile?: string;
  onSelectFile: (path: string) => void;
};

/** Tree node representing a file or folder */
type TreeNode = {
  name: string;
  path: string;
  isDirectory: boolean;
  children: TreeNode[];
  isTouched: boolean;
};

/** Get the icon for a file based on its extension */
function getFileIcon(fileName: string) {
  if (fileName.endsWith(".rs")) return FileCode2;
  if (fileName.endsWith(".json") || fileName.endsWith(".toml")) return FileJson;
  if (fileName.endsWith(".sh")) return Terminal;
  return FileCode2;
}

/** Build a tree structure from flat file paths */
function buildTree(snapshot: CodeSnapshot): TreeNode[] {
  const root: TreeNode[] = [];

  const sortedPaths = Object.keys(snapshot).sort();

  for (const filePath of sortedPaths) {
    const parts = filePath.split("/");
    let currentLevel = root;

    for (let partIndex = 0; partIndex < parts.length; partIndex++) {
      const partName = parts[partIndex];
      if (!partName) continue;
      const isFile = partIndex === parts.length - 1;
      const fullPath = parts.slice(0, partIndex + 1).join("/");

      const existing = currentLevel.find(
        (node) => node.name === partName && node.isDirectory === !isFile,
      );

      if (existing) {
        currentLevel = existing.children;
      } else {
        const fileSnap = snapshot[filePath];
        const newNode: TreeNode = {
          name: partName,
          path: fullPath,
          isDirectory: !isFile,
          children: [],
          isTouched: isFile ? (fileSnap?.touched ?? false) : false,
        };
        currentLevel.push(newNode);
        currentLevel = newNode.children;
      }
    }
  }

  /** Mark folders as touched if any child is touched */
  function propagateTouched(nodes: TreeNode[]): boolean {
    let anyTouched = false;
    for (const node of nodes) {
      if (node.isDirectory) {
        const childTouched = propagateTouched(node.children);
        node.isTouched = childTouched;
        if (childTouched) anyTouched = true;
      } else {
        if (node.isTouched) anyTouched = true;
      }
    }
    return anyTouched;
  }

  propagateTouched(root);
  return root;
}

/** Recursive tree node renderer */
function TreeNodeRow({
  node,
  depth,
  selectedFile,
  onSelectFile,
  expandedFolders,
  onToggleFolder,
}: {
  node: TreeNode;
  depth: number;
  selectedFile?: string;
  onSelectFile: (path: string) => void;
  expandedFolders: Set<string>;
  onToggleFolder: (path: string) => void;
}) {
  const isExpanded = expandedFolders.has(node.path);
  const isSelected = !node.isDirectory && selectedFile === node.path;

  if (node.isDirectory) {
    const FolderIcon = isExpanded ? FolderOpen : Folder;
    return (
      <>
        <button
          onClick={() => onToggleFolder(node.path)}
          className="flex w-full items-center gap-[5px] py-[3px] text-left text-[10px] text-envoi-text-muted transition-colors hover:bg-envoi-surface"
          style={{ paddingLeft: 8 + depth * 12 }}
        >
          <FolderIcon size={12} style={{ color: T.textDim }} />
          <span>{node.name}</span>
          {node.isTouched && (
            <Circle size={5} fill={T.accent} style={{ color: T.accent }} />
          )}
        </button>
        {isExpanded &&
          node.children.map((child) => (
            <TreeNodeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
              expandedFolders={expandedFolders}
              onToggleFolder={onToggleFolder}
            />
          ))}
      </>
    );
  }

  const FileIcon = getFileIcon(node.name);

  return (
    <button
      onClick={() => onSelectFile(node.path)}
      className={`flex w-full items-center gap-[5px] py-[3px] text-left text-[10px] transition-colors ${
        isSelected
          ? "bg-envoi-accent-bg font-semibold text-envoi-accent"
          : "text-envoi-text hover:bg-envoi-surface"
      }`}
      style={{ paddingLeft: 8 + depth * 12 }}
    >
      <FileIcon size={11} style={{ color: isSelected ? T.accent : T.textDim }} />
      <span className="truncate">{node.name}</span>
      {node.isTouched && (
        <Circle size={5} fill={T.accent} style={{ color: T.accent }} />
      )}
    </button>
  );
}

export function FileTree({ snapshot, selectedFile, onSelectFile }: FileTreeProps) {
  const tree = useMemo(() => buildTree(snapshot), [snapshot]);

  /** Start with all folders expanded */
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => {
    const folders = new Set<string>();
    function collectFolders(nodes: TreeNode[]) {
      for (const node of nodes) {
        if (node.isDirectory) {
          folders.add(node.path);
          collectFolders(node.children);
        }
      }
    }
    collectFolders(tree);
    return folders;
  });

  const handleToggleFolder = (path: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  return (
    <div className="flex flex-col overflow-y-auto border-r border-envoi-border py-[6px]" style={{ width: 190 }}>
      {tree.map((node) => (
        <TreeNodeRow
          key={node.path}
          node={node}
          depth={0}
          selectedFile={selectedFile}
          onSelectFile={onSelectFile}
          expandedFolders={expandedFolders}
          onToggleFolder={handleToggleFolder}
        />
      ))}
    </div>
  );
}
