/**
 * Small pill badge for metadata display.
 * Server-renderable.
 */

type BadgeProps = {
  children: React.ReactNode;
  color: string;
  bg: string;
};

export function Badge({ children, color, bg }: BadgeProps) {
  return (
    <span
      className="whitespace-nowrap rounded-[3px] px-1.75 py-0.5 text-[10px] font-medium"
      style={{ color, background: bg }}
    >
      {children}
    </span>
  );
}
