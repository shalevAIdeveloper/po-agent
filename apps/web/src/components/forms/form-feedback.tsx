"use client";

import { type RefObject, useEffect } from "react";

import type { FormState } from "@/lib/forms/form-state";

export const FORM_ERROR_ID = "form-error";
export const FORM_NOTICE_ID = "form-notice";

/**
 * After each submit that comes back, move focus to what needs attention: the first
 * invalid field (in `fieldOrder`, by element id), else the form error, else the
 * notice. Without this, focus is lost while the action is pending.
 */
export function useFocusAfterSubmit<Field extends string>(
  formRef: RefObject<HTMLFormElement | null>,
  state: FormState<Field>,
  fieldOrder: readonly Field[],
) {
  useEffect(() => {
    if (state.attempt === 0) return;
    const field = fieldOrder.find((name) => state.fieldErrors?.[name]);
    const id = field ?? (state.error ? FORM_ERROR_ID : state.notice ? FORM_NOTICE_ID : null);
    if (id) {
      formRef.current?.querySelector<HTMLElement>(`#${id}`)?.focus();
    }
    // fieldOrder is a constant per form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);
}

/** Form-level error and notice. New element per attempt, so repeats are announced. */
export function FormFeedback({ state }: { state: FormState<string> }) {
  return (
    <>
      {state.error ? (
        <p
          key={`error-${state.attempt}`}
          id={FORM_ERROR_ID}
          role="alert"
          tabIndex={-1}
          className="rounded-md border border-destructive/40 p-3 text-sm text-destructive outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {state.error}
        </p>
      ) : null}
      {state.notice ? (
        <p
          key={`notice-${state.attempt}`}
          id={FORM_NOTICE_ID}
          role="status"
          tabIndex={-1}
          className="rounded-md border p-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {state.notice}
        </p>
      ) : null}
    </>
  );
}

/** Announces the pending state (a disabled button's label change is not announced). */
export function PendingStatus({ pending, label }: { pending: boolean; label: string }) {
  return (
    <span role="status" className="sr-only">
      {pending ? label : ""}
    </span>
  );
}
