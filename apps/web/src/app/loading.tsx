import { LoadingState } from "@/components/states/loading-state";

export default function Loading() {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <LoadingState label="Loading" />
    </main>
  );
}
