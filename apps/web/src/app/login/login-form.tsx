"use client";

import { useActionState, useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import type { LoginState } from "@/lib/auth/login-schema";

import { login } from "./actions";

const INITIAL_STATE: LoginState = { attempt: 0 };
const FORM_ERROR_ID = "login-error";

export function LoginForm({ next }: { next?: string }) {
  const [state, formAction, pending] = useActionState(login, INITIAL_STATE);
  const formRef = useRef<HTMLFormElement>(null);

  // After each failed attempt, move focus to what needs attention: the first invalid
  // field, else the form-level alert. Without this, focus is lost while pending.
  useEffect(() => {
    if (state.attempt === 0) return;
    const selector = state.fieldErrors?.email
      ? "#email"
      : state.fieldErrors?.password
        ? "#password"
        : state.error
          ? `#${FORM_ERROR_ID}`
          : null;
    if (selector) {
      formRef.current?.querySelector<HTMLElement>(selector)?.focus();
    }
  }, [state]);

  const credentialsError = Boolean(state.error);

  return (
    <form
      ref={formRef}
      action={formAction}
      noValidate
      aria-busy={pending}
      // The button stays focusable while pending, so block a second submit via Enter.
      onSubmit={(event) => {
        if (pending) event.preventDefault();
      }}
      className="flex flex-col gap-4"
    >
      {state.error ? (
        <p
          // A new element per attempt, so an identical message is announced again.
          key={state.attempt}
          id={FORM_ERROR_ID}
          role="alert"
          tabIndex={-1}
          className="rounded-md border border-destructive/40 p-3 text-sm text-destructive outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {state.error}
        </p>
      ) : null}
      <span role="status" className="sr-only">
        {pending ? "Signing in…" : ""}
      </span>
      {next ? <input type="hidden" name="next" value={next} /> : null}
      <TextField
        id="email"
        name="email"
        type="email"
        label="Email"
        autoComplete="username"
        required
        defaultValue={state.email}
        // Remount after each attempt so defaultValue reflects the echoed email.
        key={`email-${state.attempt}`}
        error={state.fieldErrors?.email}
        invalid={credentialsError}
        describedBy={credentialsError ? FORM_ERROR_ID : undefined}
      />
      <TextField
        id="password"
        name="password"
        type="password"
        label="Password"
        autoComplete="current-password"
        required
        error={state.fieldErrors?.password}
        invalid={credentialsError}
        describedBy={credentialsError ? FORM_ERROR_ID : undefined}
      />
      <Button
        type="submit"
        size="lg"
        disabled={pending}
        // aria-disabled instead of `disabled`: keeps focus on the button while pending.
        focusableWhenDisabled
        className="data-disabled:opacity-50"
      >
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
