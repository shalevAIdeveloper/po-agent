"use client";

import Link from "next/link";
import { useActionState, useRef } from "react";

import { FormFeedback, PendingStatus, useFocusAfterSubmit } from "@/components/forms/form-feedback";
import { Button } from "@/components/ui/button";
import type { EmailLink } from "@/lib/auth/email-link";

import { type ConfirmState, confirmEmailLink } from "./actions";

const INITIAL_STATE: ConfirmState = { attempt: 0 };
const NO_FIELDS: readonly never[] = [];

export function ConfirmForm({ link, actionLabel }: { link: EmailLink; actionLabel: string }) {
  const [state, formAction, pending] = useActionState(confirmEmailLink, INITIAL_STATE);
  const formRef = useRef<HTMLFormElement>(null);
  useFocusAfterSubmit(formRef, state, NO_FIELDS);

  return (
    <form
      ref={formRef}
      action={formAction}
      aria-busy={pending}
      onSubmit={(event) => {
        if (pending) event.preventDefault();
      }}
      className="flex flex-col gap-4"
    >
      <FormFeedback state={state} />
      <PendingStatus pending={pending} label="Checking your link…" />
      <input type="hidden" name="type" value={link.type} />
      <input type="hidden" name="token_hash" value={link.tokenHash} />
      {state.expired ? (
        <RequestNewLink />
      ) : (
        <Button
          type="submit"
          size="lg"
          disabled={pending}
          focusableWhenDisabled
          className="data-disabled:opacity-50"
        >
          {pending ? "Checking…" : actionLabel}
        </Button>
      )}
    </form>
  );
}

export function RequestNewLink() {
  return (
    <p className="text-sm text-muted-foreground">
      Invitation links come from your organization&apos;s admin. To reset your password,{" "}
      <Link
        href="/forgot-password"
        className="rounded-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        request a new link
      </Link>
      .
    </p>
  );
}
