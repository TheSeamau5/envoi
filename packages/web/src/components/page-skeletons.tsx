import { PageHeader, ShellRow } from "@/components/page-shell";

function PulseBar({
  className,
}: {
  className: string;
}) {
  return <div className={`animate-pulse rounded bg-envoi-surface ${className}`} />;
}

/** Cold-load skeleton for the trajectory list page. */
export function TrajectoryListSkeleton() {
  return (
    <div className="flex w-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader
        title="Trajectories"
        right={<span>Loading cached runs...</span>}
      />
      <div className="flex shrink-0 items-center border-b border-envoi-border bg-envoi-surface px-3.5 py-1.5">
        <PulseBar className="h-3 w-[260px]" />
        <PulseBar className="ml-3 h-3 w-[64px]" />
        <PulseBar className="ml-3 h-3 w-[48px]" />
        <PulseBar className="ml-3 h-3 w-[40px]" />
        <PulseBar className="ml-3 h-3 w-35" />
        <PulseBar className="ml-3 h-3 w-[80px]" />
        <PulseBar className="ml-3 h-3 flex-1" />
        <PulseBar className="ml-3 h-3 w-[72px]" />
      </div>
      <div className="flex-1 overflow-hidden px-3.5 py-2">
        {Array.from({ length: 10 }).map((_, index) => (
          <div
            key={index}
            className="flex items-center gap-3 border-b border-envoi-border-light py-2.5"
          >
            <PulseBar className="h-3 w-[220px]" />
            <PulseBar className="h-3 w-[56px]" />
            <PulseBar className="h-3 w-[40px]" />
            <PulseBar className="h-3 w-[32px]" />
            <PulseBar className="h-3 w-[120px]" />
            <PulseBar className="h-3 w-[72px]" />
            <PulseBar className="h-3 flex-1" />
            <PulseBar className="h-3 w-[56px]" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** Cold-load skeleton for the compare layout shell. */
export function CompareShellSkeleton() {
  return (
    <div className="flex w-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader title="Compare" right={<span>Loading compare data...</span>} />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="flex w-70 shrink-0 flex-col border-r border-envoi-border">
          <ShellRow>
            <PulseBar className="h-3 w-32" />
          </ShellRow>
          <ShellRow tone="plain" className="gap-2">
            <PulseBar className="h-3 w-28" />
            <div className="flex-1" />
            <PulseBar className="h-3 w-16" />
          </ShellRow>
          <div className="flex-1 overflow-hidden">
            {Array.from({ length: 8 }).map((_, index) => (
              <div
                key={index}
                className="flex items-center gap-3 border-b border-envoi-border-light px-3.5 py-2.5"
              >
                <PulseBar className="h-2 w-2 rounded-full" />
                <div className="min-w-0 flex-1">
                  <PulseBar className="h-3 w-36" />
                  <PulseBar className="mt-1 h-3 w-24" />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <ShellRow>
            <PulseBar className="h-3 w-40" />
          </ShellRow>
          <ShellRow tone="plain">
            <PulseBar className="h-3 w-56" />
          </ShellRow>
          <div className="flex-1 p-4">
            <div className="h-full animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** Cold-load skeleton for the setups page. */
export function SetupsPageSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <div className="flex w-65 shrink-0 flex-col border-r border-envoi-border">
        <ShellRow>
          <PulseBar className="h-3 w-24" />
        </ShellRow>
        <ShellRow tone="plain" className="gap-2 overflow-hidden">
          <PulseBar className="h-7 w-[72px]" />
          <PulseBar className="h-7 w-[72px]" />
          <PulseBar className="h-7 w-[72px]" />
        </ShellRow>
        <ShellRow>
          <PulseBar className="h-3 w-20" />
        </ShellRow>
        <div className="flex-1 overflow-hidden">
          {Array.from({ length: 7 }).map((_, index) => (
            <div
              key={index}
              className="flex items-center gap-3 border-b border-envoi-border-light px-3.5 py-2.5"
            >
              <PulseBar className="h-2 w-2 rounded-full" />
              <div className="min-w-0 flex-1">
                <PulseBar className="h-3 w-28" />
                <PulseBar className="mt-1 h-3 w-20" />
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden p-4">
        <div className="mb-4 h-72 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
        <div className="mb-4 h-52 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
        <div className="h-52 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
      </div>
    </div>
  );
}

/** Cold-load skeleton for the SQL console page. */
export function QueryPageSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <div className="flex w-70 shrink-0 flex-col border-r border-envoi-border">
        <ShellRow>
          <PulseBar className="h-3 w-24" />
        </ShellRow>
        <div className="flex-1 overflow-hidden px-3 py-2">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="border-b border-envoi-border-light py-2">
              <PulseBar className="h-3 w-36" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col border-r border-envoi-border">
        <ShellRow tone="plain" className="justify-between">
          <PulseBar className="h-3 w-24" />
          <PulseBar className="h-7 w-[72px]" />
        </ShellRow>
        <div className="flex-1 p-3">
          <div className="h-40 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
          <div className="mt-3 h-[calc(100%-11rem)] animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
        </div>
      </div>
      <div className="flex w-72 shrink-0 flex-col">
        <ShellRow>
          <PulseBar className="h-3 w-16" />
        </ShellRow>
        <div className="flex-1 overflow-hidden px-3 py-2">
          {Array.from({ length: 10 }).map((_, index) => (
            <div key={index} className="border-b border-envoi-border-light py-2">
              <PulseBar className="h-3 w-28" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Cold-load skeleton for the portfolio page. */
export function PortfolioPageSkeleton() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex-1 overflow-auto p-4">
        <div className="mb-4 h-44 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
        <div className="mb-4 h-72 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
        <div className="h-72 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
      </div>
    </div>
  );
}

/** Cold-load skeleton for the difficulty page. */
export function DifficultyPageSkeleton() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex-1 overflow-auto px-3.5 py-3.5">
        <PulseBar className="mb-3 h-3 w-[528px]" />
        <PulseBar className="mb-4 h-3 w-[464px]" />
        <div className="h-96 animate-pulse rounded border border-envoi-border bg-envoi-surface/40" />
      </div>
    </div>
  );
}
