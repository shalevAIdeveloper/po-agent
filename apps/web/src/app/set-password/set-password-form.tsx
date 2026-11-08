"use client";

import { useActionState, useRef } from "react";

import { FormFeedback, PendingStatus, useFocusAfterSubmit } from "@/components/forms/form-feedback";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { PASSWORD_MIN_CHARS } from "@/lib/auth/password";

import { type SetPasswordField, type SetPasswordState, setPassword } from "./actions";

const INITIAL_STATE: SetPasswordState = { attempt: 0 };
const FIELD_ORDER: readonly SetPasswordField[] = ["password", "confirm"];
const HINT_ID = "password-hint";

export function SetPasswordForm({ email }: { email: string | null }) {
  const [state, formAction, pending] = useActionState(setPassword, INITIAL_STATE);
  const formRef = useRef<HTMLFormElement>(null);
  useFocusAfterSubmit(formRef, state, FIELD_ORDER);

  return (
    <form
      ref={formRef}
      action={formAction}
      noValidate
      aria-busy={pending}
      onSubmit={(event) => {
        if (pending) event.preventDefault();
      }}
      className="flex flex-col gap-4"
    >
      <FormFeedback state={state} />
      <PendingStatus pending={pending} label="Saving your password…" />
      {/* Lets password managers save the new password under the right account. */}
      {email ? (
        <input type="email" name="username" autoComplete="username" value={email} readOnly hidden />
      ) : null}
      <TextField
        id="password"
        name="password"
        type="password"
        label="New password"
        autoComplete="new-password"
        required
        // Remount after each attempt: passwords are never echoed back.
        key={`password-${state.attempt}`}
        error={state.fieldErrors?.password}
        describedBy={HINT_ID}
      />
      <p id={HINT_ID} className="-mt-2 text-sm text-muted-foreground">
        At least {PASSWORD_MIN_CHARS} characters. A short phrase is easier to remember than
        symbols.
      </p>
      <TextField
        id="confirm"
        name="confirm"
        type="password"
        label="Confirm new password"
        autoComplete="new-password"
        required
        key={`confirm-${state.attempt}`}
        error={state.fieldErrors?.confirm}
      />
      <Button
        type="submit"
        size="lg"
        disabled={pending}
        focusableWhenDisabled
        className="data-disabled:opacity-50"
      >
        {pending ? "Saving…" : "Save password"}
      </Button>
    </form>
  );
}
