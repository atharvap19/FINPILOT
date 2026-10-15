import { AccountGate } from "@/components/account/account-context";
import { AppHeader } from "@/components/account/app-header";
import { SiteFooter } from "@/components/legal/site-footer";
import { SourcesPage } from "@/components/sources/sources-page";

export default function Page() {
  return (
    <AccountGate>
      <AppHeader />
      <SourcesPage />
      <SiteFooter />
    </AccountGate>
  );
}
