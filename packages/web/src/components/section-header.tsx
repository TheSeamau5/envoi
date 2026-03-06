/**
 * ALL CAPS section header — used throughout for label rows.
 * Server-renderable.
 */

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-12 shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5 text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
      {children}
    </div>
  );
}
