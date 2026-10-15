import type { Metadata } from "next";

import { H2, LegalPage, List } from "@/components/legal/legal-page";
import { LEGAL } from "@/lib/legal";

export const metadata: Metadata = { title: "Terms of Service — FinPilot" };

export default function TermsPage() {
  const { product, operator, jurisdiction, supportEmail } = LEGAL;
  return (
    <LegalPage title="Terms of Service">
      <p>
        These terms are an agreement between you and {operator} for your use of {product}. By creating an account
        you accept them and our <a href="/privacy" className="underline">Privacy Policy</a>.
      </p>

      <H2>1. What {product} does</H2>
      <p>
        {product} reads financial documents you upload, organises them into a ledger, groups your spending into
        habits, and answers questions and writes reports about it using an AI model.
      </p>

      <H2>2. Not financial advice</H2>
      <p>
        {product} provides information and analysis of your own spending. It does <b>not</b> provide financial,
        investment, tax, legal or credit advice, and {product} is not a SEBI-registered investment adviser.
        AI-generated answers and reports can be wrong or incomplete. Check important figures against your
        original statements and consult a qualified professional before making financial decisions. You are
        responsible for decisions you make.
      </p>

      <H2>3. Your account</H2>
      <List>
        <li>You must be 18 or older and give accurate details.</li>
        <li>Keep your password secret; you are responsible for activity on your account.</li>
        <li>Only upload documents that belong to you or that you are authorised to share.</li>
      </List>

      <H2>4. Acceptable use</H2>
      <p>Do not:</p>
      <List>
        <li>try to access other users&rsquo; data, or probe, overload or disrupt the service;</li>
        <li>upload malware, or content that is unlawful or that you have no right to process;</li>
        <li>use {product} to commit fraud or to process someone else&rsquo;s data without their consent;</li>
        <li>copy, resell or reverse-engineer the service.</li>
      </List>

      <H2>5. Your content</H2>
      <p>
        You keep all rights in what you upload. You allow us to process it only to provide {product} to you, as
        described in the Privacy Policy. You can download or delete it at any time.
      </p>

      <H2>6. Plans and payment</H2>
      <List>
        <li>The Free plan has monthly limits shown in the app. Pro is prepaid for one month or one year and does <b>not</b> renew automatically.</li>
        <li>Prices are shown in Indian rupees before checkout{" "}[and include / exclude GST]. Payments are processed by Razorpay.</li>
        <li>&ldquo;Unlimited&rdquo; features are subject to the fair-use limits shown in the app, which protect the service for everyone.</li>
        <li>Refunds are governed by our <a href="/refunds" className="underline">Refund &amp; Cancellation Policy</a>.</li>
      </List>

      <H2>7. Availability and changes</H2>
      <p>
        We work to keep {product} available and accurate but provide it &ldquo;as is&rdquo;, without guarantees of
        uninterrupted service or error-free results. We may change or discontinue features; if we discontinue a
        paid feature you have prepaid for, we will refund the unused part.
      </p>

      <H2>8. Liability</H2>
      <p>
        To the extent the law allows, we are not liable for indirect or consequential losses, or for decisions you
        make based on {product}&rsquo;s output. Our total liability to you is limited to the amount you paid us in the
        twelve months before the claim. Nothing in these terms limits liability that cannot be limited by law.
      </p>

      <H2>9. Ending your use</H2>
      <p>
        You can delete your account at any time from the app. We may suspend or close accounts that break these
        terms, after notice where reasonable.
      </p>

      <H2>10. Law and disputes</H2>
      <p>
        These terms are governed by the laws of India. Courts at {jurisdiction} have exclusive jurisdiction. Please
        contact us first at {supportEmail} — most problems are solved faster that way.
      </p>
    </LegalPage>
  );
}
