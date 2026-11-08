/** State returned by a form's Server Action (useActionState). */
export type FormState<Field extends string> = {
  /** Increments on every submit that comes back, so the UI re-announces and re-focuses. */
  attempt: number;
  /** Form-level error, shown in an alert above the fields. */
  error?: string;
  /** Form-level success/info message (e.g. "check your email"), shown in a status region. */
  notice?: string;
  fieldErrors?: Partial<Record<Field, string>>;
  /** Echoed back so the user doesn't retype them. Never passwords. */
  values?: Partial<Record<Field, string>>;
};

/** `previous` round-trips through the client: never trust its shape. */
export function nextAttempt(previous: { attempt?: unknown } | null | undefined): number {
  const attempt = previous?.attempt;
  return (typeof attempt === "number" && Number.isSafeInteger(attempt) && attempt >= 0 ? attempt : 0) + 1;
}

/** First message per field from Zod-style issues. */
export function fieldErrorsFrom<Field extends string>(
  issues: ReadonlyArray<{ path: ReadonlyArray<PropertyKey>; message: string }>,
  fields: readonly Field[],
): Partial<Record<Field, string>> {
  const errors: Partial<Record<Field, string>> = {};
  for (const issue of issues) {
    const field = issue.path[0];
    if (typeof field === "string" && (fields as readonly string[]).includes(field)) {
      const key = field as Field;
      errors[key] ??= issue.message;
    }
  }
  return errors;
}

export function formString(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value : "";
}
