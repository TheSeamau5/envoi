/**
 * Collapsible sidebar navigation.
 * Client component — manages collapse state with localStorage persistence.
 * Claude-style: collapsed shows icons only, no text.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  GitCommitHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { usePersistedState } from "@/lib/storage";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const NAV_ITEMS = [
  { href: "/compare", label: "Compare", icon: BarChart3 },
  { href: "/trajectory", label: "Trajectories", icon: GitCommitHorizontal },
] as const;

export function Sidebar() {
  const [collapsed, setCollapsed] = usePersistedState("sidebar-collapsed", false);
  const pathname = usePathname();

  return (
    <div
      className="flex flex-col border-r border-envoi-border bg-envoi-bg"
      style={{ width: collapsed ? 48 : 200, transition: "width 0.15s ease" }}
    >
      {/* Logo */}
      <div className="flex h-[41px] shrink-0 items-center border-b border-envoi-border px-3">
        {!collapsed && (
          <span className="text-sm font-bold text-envoi-accent">envoi</span>
        )}
      </div>

      {/* Navigation */}
      <div className="flex-1 py-2">
        {!collapsed && (
          <div className="px-3 pb-2">
            <span className="text-[9px] font-semibold uppercase tracking-[0.06em] text-envoi-text-dim">
              Navigation
            </span>
          </div>
        )}
        {NAV_ITEMS.map((item) => {
          const isActive =
            pathname === item.href || pathname.startsWith(item.href + "/");
          const Icon = item.icon;

          const linkContent = (
            <Link
              href={item.href}
              className={`flex items-center ${collapsed ? "justify-center" : "gap-2"} px-3 py-2 text-[11px] transition-colors ${
                isActive
                  ? collapsed
                    ? "text-envoi-accent"
                    : "border-l-[3px] border-envoi-accent text-envoi-accent"
                  : collapsed
                    ? "text-envoi-text-muted hover:bg-envoi-surface"
                    : "border-l-[3px] border-transparent text-envoi-text-muted hover:bg-envoi-surface"
              }`}
            >
              <Icon size={14} />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );

          if (collapsed) {
            return (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            );
          }

          return <div key={item.href}>{linkContent}</div>;
        })}
      </div>

      {/* Collapse toggle */}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={() => setCollapsed((prev) => !prev)}
            className="flex items-center justify-center border-t border-envoi-border py-2 text-envoi-text-dim hover:text-envoi-text"
          >
            {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
          </button>
        </TooltipTrigger>
        {collapsed && (
          <TooltipContent side="right">Expand sidebar</TooltipContent>
        )}
      </Tooltip>
    </div>
  );
}
