import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type ErrorStateProps = {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
};

export function ErrorState({ title, description, action, className }: ErrorStateProps) {
  return (
    <div
      role="alert"
      data-slot="error-state"
      className={cn(
        "flex flex-col items-start gap-2 rounded-lg border border-destructive/40 p-6",
        className,
      )}
    >
      <p className="font-medium text-destructive">{title}</p>
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
