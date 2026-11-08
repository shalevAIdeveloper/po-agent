import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

type TextFieldProps = Omit<ComponentProps<"input">, "id" | "aria-invalid" | "aria-describedby"> & {
  id: string;
  label: string;
  /** This field's own message, shown under it. */
  error?: string;
  /** A form-level message (by id) that also describes this field, e.g. wrong credentials. */
  describedBy?: string;
  /** Marks the field invalid without a message of its own (see `describedBy`). */
  invalid?: boolean;
};

/** Labelled input with its error messages wired to it for screen readers. */
export function TextField({
  id,
  label,
  error,
  describedBy,
  invalid,
  className,
  ...props
}: TextFieldProps) {
  const errorId = `${id}-error`;
  const describedByIds = [error ? errorId : null, describedBy ?? null].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error || invalid ? true : undefined}
        aria-describedby={describedByIds || undefined}
        className={cn(
          "h-9 rounded-md border border-input bg-background px-3 text-sm transition-colors",
          "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none",
          "aria-invalid:border-destructive disabled:opacity-50",
          className,
        )}
        {...props}
      />
      {error ? (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
