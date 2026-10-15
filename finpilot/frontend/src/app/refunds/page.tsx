import type { Metadata } from "next";

import { H2, LegalPage, List } from "@/components/legal/legal-page";
import { LEGAL } from "@/lib/legal";

export const metadata: Metadata = { title: "Refund & Cancellation Policy — FinPilot" };

export default function RefundsPage() {
  const { product, supportEmail, refundWindowDays } = LEGAL;
  return (
    <LegalPage title="Refund & Cancellation Policy">
      <H2>Pro is prepaid and never auto-renews</H2>
      <p>
        {product} Pro is bought for one month or one year at a time. We never charge you again automatically, so
        there is nothing to cancel: when your period ends, your account simply returns to the Free plan and keeps
        all your data.
      </p>

      <H2>Refunds</H2>
      <List>
        <li><b>First purchase, within {refundWindowDays} days:</b> if Pro isn&rsquo;t for you, email {supportEmail} within {refundWindowDays} days of your first Pro purchase for a full refund.</li>
        <li><b>Duplicate or failed payments:</b> if you were charged twice, or charged without Pro being activated, we refund the extra or failed charge in full.</li>
        <li><b>After {refundWindowDays} days, and on renewals:</b> payments are not refundable for the time already started, except where the law requires otherwise or we discontinue a paid feature.</li>
      </List>

      <H2>How refunds are paid</H2>
      <p>
        Approved refunds go back to the original payment method through Razorpay, usually within 5–7 business days
        of approval (your bank may take longer to show it). There is no physical product, so nothing is shipped or
        returned.
      </p>

      <H2>Questions</H2>
      <p>
        Write to {supportEmail} with your account email and the order reference from your payment confirmation.
        See also our <a href="/contact" className="underline">Contact &amp; Grievances</a> page.
      </p>
    </LegalPage>
  );
}
