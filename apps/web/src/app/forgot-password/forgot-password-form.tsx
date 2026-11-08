"use client";

import Link from "next/link";
import { useActionState, useRef } from "react";

import { FormFeedback, PendingStatus, useFocusAfterSubmit } from "@/components/forms/form-feedback";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";

import { type ForgotPasswordState, requestPasswordReset } from "./actions";

const INITIAL_STATE: ForgotPasswordState = { attempt: 0 };
const FIELD_ORDER = ["email"] as const;

export function ForgotPasswordForm() {
  const [state, formAction, pending] = useActionState(requestPasswordReset, INITIAL_STATE);
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
      <PendingStatus pending={pending} label="Sending…" />
      <TextField
        id="email"
        name="email"
        type="email"
        label="Email"
        autoComplete="username"
        required
        defaultValue={state.values?.email}
        key={`email-${state.attempt}`}
        error={state.fieldErrors?.email}
      />
      <Button
        type="submit"
        size="lg"
        disabled={pending}
        focusableWhenDisabled
        className="data-disabled:opacity-50"
      >
        {pending ? "Sending…" : "Send reset link"}
      </Button>
      <p className="text-sm">
        <Link
          href="/login"
          className="rounded-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Back to sign in
        </Link>
      </p>
    </form>
  );
}
