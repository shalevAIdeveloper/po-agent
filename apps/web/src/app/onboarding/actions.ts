"use server";

import { redirect } from "next/navigation";
import { z } from "zod";

import { api, ApiError } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { requireUser } from "@/lib/auth/session";
import { type FormState, fieldErrorsFrom, formString, nextAttempt } from "@/lib/forms/form-state";
import { isValidSlug, SLUG_MAX, SLUG_MIN } from "@/lib/orgs/slug";

export type OnboardingField = "name" | "slug";
export type OnboardingState = FormState<OnboardingField>;

const FIELDS: readonly OnboardingField[] = ["name", "slug"];

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { error: "Enter your organization's name." })
    .max(200, { error: "Use at most 200 characters." }),
  slug: z.string().refine(isValidSlug, {
    error: `Use ${SLUG_MIN}–${SLUG_MAX} lowercase letters, numbers and single hyphens.`,
  }),
});

export async function createOrganization(
  previous: OnboardingState,
  formData: FormData,
): Promise<OnboardingState> {
  const attempt = nextAttempt(previous);
  const user = await requireUser();

  const values = { name: formString(formData, "name"), slug: formString(formData, "slug").trim() };
  const parsed = schema.safeParse(values);
  if (!parsed.success) {
    return { attempt, values, fieldErrors: fieldErrorsFrom(parsed.error.issues, FIELDS) };
  }

  try {
    await api.createOrganization(user.accessToken, parsed.data);
  } catch (error) {
    return { attempt, values, ...createErrorState(error) };
  }
  redirect("/app");
}

function createErrorState(error: unknown): Pick<OnboardingState, "error" | "fieldErrors"> {
  if (error instanceof ApiError && error.kind === "http") {
    switch (error.code) {
      // Taken by another organization, or a reserved word: one answer for both.
      case "slug_unavailable":
      case "invalid_organization":
        return { fieldErrors: { slug: "This address isn't available. Try another one." } };
      case "organization_limit_reached":
        return { error: "You've reached the limit of organizations you can create." };
    }
    if (error.status === 401) {
      return { error: "Your session has ended. Sign in again to continue." };
    }
  }
  const message = describeApiError(error);
  return { error: `${message.title}. ${message.description}` };
}
