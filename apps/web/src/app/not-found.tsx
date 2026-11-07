import Link from "next/link";

import { EmptyState } from "@/components/states/empty-state";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <EmptyState
        title="Page not found"
        description="The page you are looking for does not exist."
        action={
          <Link href="/" className="text-sm underline underline-offset-4">
            Back to home
          </Link>
        }
      />
    </main>
  );
}
