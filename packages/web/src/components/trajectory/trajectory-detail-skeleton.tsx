/** Loading skeleton for the trajectory detail page — shown immediately via Suspense. */

export function TrajectoryDetailSkeleton() {
  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left panel skeleton */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Progress curve placeholder */}
        <div className="h-[200px] border-b border-envoi-border bg-envoi-surface/30">
          <div className="flex h-full items-center justify-center text-[13px] text-envoi-text-dim">
            Loading trajectory...
          </div>
        </div>

        {/* Controls placeholder */}
        <div className="flex h-10 items-center border-b border-envoi-border px-3.5">
          <div className="h-3 w-32 animate-pulse rounded bg-envoi-surface" />
        </div>

        {/* Suite filter placeholder */}
        <div className="flex items-center gap-1 border-b border-envoi-border px-3.5 py-1.5">
          {Array.from({ length: 4 }).map((_, index) => (
            <div
              key={index}
              className="h-5 w-14 animate-pulse rounded-full bg-envoi-surface"
            />
          ))}
        </div>

        {/* Commit list placeholder */}
        <div className="flex-1 overflow-hidden">
          {Array.from({ length: 8 }).map((_, index) => (
            <div
              key={index}
              className="flex items-center gap-3 border-b border-envoi-border/50 px-3.5 py-2.5"
            >
              <div className="h-3 w-8 animate-pulse rounded bg-envoi-surface" />
              <div className="h-3 flex-1 animate-pulse rounded bg-envoi-surface" />
              <div className="h-3 w-12 animate-pulse rounded bg-envoi-surface" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
