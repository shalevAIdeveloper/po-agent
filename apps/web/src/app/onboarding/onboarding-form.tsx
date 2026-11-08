"use client";

import { useActionState, useRef, useState } from "react";

import { FormFeedback, PendingStatus, useFocusAfterSubmit } from "@/components/forms/form-feedback";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { deriveSlug, SLUG_MAX } from "@/lib/orgs/slug";

import { createOrganization, type OnboardingField, type OnboardingState } from "./actions";

const INITIAL_STATE: OnboardingState = { attempt: 0 };
const FIELD_ORDER: readonly OnboardingField[] = ["name", "slug"];
const SLUG_HINT_ID = "slug-hint";

export function OnboardingForm() {
  const [state, formAction, pending] = useActionState(createOrganization, INITIAL_STATE);
  const formRef = useRef<HTMLFormElement>(null);
  useFocusAfterSubmit(formRef, state, FIELD_ORDER);

  // The slug follows the name until the user edits it.
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);

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
      <PendingStatus pending={pending} label="Creating your organization…" />
      <TextField
        id="name"
        name="name"
        label="Organization name"
        autoComplete="organization"
        required
        maxLength={200}
        value={name}
        onChange={(event) => {
          setName(event.target.value);
          if (!slugEdited) setSlug(deriveSlug(event.target.value));
        }}
        error={state.fieldErrors?.name}
      />
      <TextField
        id="slug"
        name="slug"
        label="Short name"
        required
        maxLength={SLUG_MAX}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        value={slug}
        onChange={(event) => {
          setSlugEdited(true);
          setSlug(event.target.value.toLowerCase());
        }}
        error={state.fieldErrors?.slug}
        describedBy={SLUG_HINT_ID}
      />
      <p id={SLUG_HINT_ID} className="-mt-2 text-sm text-muted-foreground">
        Lowercase letters, numbers and hyphens. It becomes part of your organization&apos;s order
        email address and can&apos;t be changed later.
      </p>
      <Button
        type="submit"
        size="lg"
        disabled={pending}
        focusableWhenDisabled
        className="data-disabled:opacity-50"
      >
        {pending ? "Creating…" : "Create organization"}
      </Button>
    </form>
  );
}
