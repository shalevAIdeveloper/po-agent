import { cn } from "@/lib/utils";

type LoadingStateProps = {
  /** Announced to screen readers; not shown visually. */
  label: string;
  lines?: number;
  className?: string;
};

export function LoadingState({ label, lines = 3, className }: LoadingStateProps) {
  return (
    // No aria-busy here: it can mute the live region. Put aria-busy on the
    // container being loaded (e.g. the table), not on this status message.
    <div role="status" className={cn("flex flex-col gap-2", className)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          data-slot="skeleton"
          className="h-4 rounded-md bg-muted motion-safe:animate-pulse last:w-2/3"
        />
      ))}
    </div>
  );
}
