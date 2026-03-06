import type { ReactNode } from "react";

export const PAGE_HEADER_CLASS =
  "flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4";
export const SHELL_SURFACE_ROW_CLASS =
  "flex h-12 shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5";
export const SHELL_PLAIN_ROW_CLASS =
  "flex h-12 shrink-0 items-center border-b border-envoi-border px-3.5";

type PageHeaderProps = {
  title: ReactNode;
  right?: ReactNode;
};

/** Shared top page header row that aligns with the persistent app sidebar. */
export function PageHeader({ title, right }: PageHeaderProps) {
  return (
    <div className={PAGE_HEADER_CLASS}>
      <div className="min-w-0 flex-1">
        <span className="truncate text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          {title}
        </span>
      </div>
      {right ? (
        <div className="ml-4 flex shrink-0 items-center gap-2 text-[12px] text-envoi-text-dim">
          {right}
        </div>
      ) : null}
    </div>
  );
}

type ShellRowProps = {
  children?: ReactNode;
  tone?: "surface" | "plain";
  className?: string;
};

/** Shared fixed-height structural row for split panes and page-specific shells. */
export function ShellRow({
  children,
  tone = "surface",
  className = "",
}: ShellRowProps) {
  const baseClass =
    tone === "surface" ? SHELL_SURFACE_ROW_CLASS : SHELL_PLAIN_ROW_CLASS;
  return <div className={`${baseClass} ${className}`.trim()}>{children}</div>;
}
