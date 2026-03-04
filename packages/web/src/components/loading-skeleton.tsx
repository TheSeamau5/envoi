/** Generic loading skeleton — rows of shimmering bars. */

export function LoadingSkeleton({ message = "Loading..." }: { message?: string }) {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex h-12 items-center border-b border-envoi-border px-4">
        <span className="text-[13px] text-envoi-text-dim">{message}</span>
      </div>
      <div className="flex-1 overflow-hidden px-4 pt-3">
        {Array.from({ length: 10 }).map((_, index) => (
          <div
            key={index}
            className="flex items-center gap-3 pb-2.5"
          >
            <div className="h-3 w-20 animate-pulse rounded bg-envoi-surface" />
            <div className="h-3 flex-1 animate-pulse rounded bg-envoi-surface" />
            <div className="h-3 w-16 animate-pulse rounded bg-envoi-surface" />
          </div>
        ))}
      </div>
    </div>
  );
}
