"use client";

import Link from "next/link";
import { useActionState, useRef } from "react";

import {
  FORM_ERROR_ID,
  FormFeedback,
  PendingStatus,
  useFocusAfterSubmit,
} from "@/components/forms/form-feedback";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import type { LoginField, LoginState } from "@/lib/auth/login-schema";

import { login } from "./actions";

const INITIAL_STATE: LoginState = { attempt: 0 };
const FIELD_ORDER: readonly LoginField[] = ["email", "password"];

export function LoginForm({ next }: { next?: string }) {
  const [state, formAction, pending] = useActionState(login, INITIAL_STATE);
  const formRef = useRef<HTMLFormElement>(null);
  useFocusAfterSubmit(formRef, state, FIELD_ORDER);

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
      <FormFeedback state={state} />
      <PendingStatus pending={pending} label="Signing in…" />
      {next ? <input type="hidden" name="next" value={next} /> : null}
      <TextField
        id="email"
        name="email"
        type="email"
        label="Email"
        autoComplete="username"
        required
        defaultValue={state.values?.email}
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
      <p className="text-sm">
        <Link
          href="/forgot-password"
          className="rounded-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Forgot your password?
        </Link>
      </p>
    </form>
  );
}
