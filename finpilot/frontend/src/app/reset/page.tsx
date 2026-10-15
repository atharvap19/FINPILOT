import type { Metadata } from "next";

import { ResetForm } from "@/components/account/reset-form";
import { SiteFooter } from "@/components/legal/site-footer";

export const metadata: Metadata = { title: "Reset your password — FinPilot" };

export default async function ResetPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { token } = await searchParams;
  return (
    <div className="fp-hero-bg flex min-h-screen flex-col">
      <main className="flex flex-1 items-center justify-center px-6 py-16">
        <ResetForm token={typeof token === "string" ? token : ""} />
      </main>
      <SiteFooter />
    </div>
  );
}
