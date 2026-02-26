/**
 * Collapsible sidebar navigation.
 * Client component — manages collapse state.
 */

"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  GitCommitHorizontal,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/compare", label: "Compare", icon: BarChart3 },
  { href: "/trajectory", label: "Trajectories", icon: GitCommitHorizontal },
] as const;

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();

  return (
    <div
      className="flex flex-col border-r border-envoi-border bg-envoi-bg"
      style={{ width: collapsed ? 48 : 200, transition: "width 0.15s ease" }}
    >
      {/* Logo */}
      <div className="flex items-center gap-2 border-b border-envoi-border px-3 py-3">
        <span className="text-sm font-bold text-envoi-accent">envoi</span>
      </div>

      {/* Navigation */}
      <div className="flex-1 py-2">
        <div className="px-3 pb-2">
          {!collapsed && (
            <span className="text-[9px] font-semibold uppercase tracking-[0.06em] text-envoi-text-dim">
              Navigation
            </span>
          )}
        </div>
        {NAV_ITEMS.map((item) => {
          const isActive =
            pathname === item.href || pathname.startsWith(item.href + "/");
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 px-3 py-2 text-[11px] transition-colors ${
                isActive
                  ? "border-l-[3px] border-envoi-accent text-envoi-accent"
                  : "border-l-[3px] border-transparent text-envoi-text-muted hover:bg-envoi-surface"
              }`}
            >
              <Icon size={14} />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );
        })}
      </div>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex items-center justify-center border-t border-envoi-border py-2 text-envoi-text-dim hover:text-envoi-text"
      >
        {collapsed ? <ChevronsRight size={14} /> : <ChevronsLeft size={14} />}
      </button>
    </div>
  );
}
