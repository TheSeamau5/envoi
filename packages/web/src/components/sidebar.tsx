/**
 * Collapsible sidebar navigation.
 * Client component — collapse state persisted via cookie so SSR matches hydration.
 * Animated with react-spring (skipped on first render to prevent flash).
 */

"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  GitCommitHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useSpring, animated } from "@react-spring/web";
import { setLayoutCookie } from "@/lib/cookies.client";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const NAV_ITEMS = [
  { href: "/compare", label: "Compare", icon: BarChart3 },
  { href: "/trajectory", label: "Trajectories", icon: GitCommitHorizontal },
] as const;

type SidebarProps = {
  /** Server-read initial value — eliminates FOUC on collapse state */
  initialCollapsed: boolean;
};

export function Sidebar({ initialCollapsed }: SidebarProps) {
  const [collapsed, setCollapsedRaw] = useState(initialCollapsed);
  const pathname = usePathname();
  const [isFirstRender, setIsFirstRender] = useState(true);

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedRaw(next);
    setLayoutCookie("sidebarCollapsed", next);
  }, []);

  const spring = useSpring({
    width: collapsed ? 48 : 200,
    contentOpacity: collapsed ? 0 : 1,
    config: { tension: 300, friction: 30 },
    immediate: isFirstRender,
    onRest: () => {
      setIsFirstRender(false);
    },
  });

  return (
    <animated.div
      className="flex shrink-0 flex-col overflow-hidden border-r border-envoi-border bg-envoi-bg"
      style={{ width: spring.width }}
    >
      {/* Header: logo + collapse toggle */}
      <div className={`flex h-10.25 shrink-0 items-center border-b border-envoi-border ${collapsed ? "justify-center" : "px-3"}`}>
        {!collapsed && (
          <animated.span
            className="min-w-0 flex-1 overflow-hidden text-sm font-bold whitespace-nowrap text-envoi-accent"
            style={{ opacity: spring.contentOpacity }}
          >
            envoi
          </animated.span>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-envoi-text-dim hover:bg-envoi-surface hover:text-envoi-text"
        >
          {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
        </button>
      </div>

      {/* Navigation */}
      <div className="flex-1 py-2">
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
              {!collapsed && <span className="whitespace-nowrap">{item.label}</span>}
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
    </animated.div>
  );
}
