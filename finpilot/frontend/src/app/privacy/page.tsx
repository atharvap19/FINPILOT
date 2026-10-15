import type { Metadata } from "next";

import { H2, LegalPage, List } from "@/components/legal/legal-page";
import { LEGAL } from "@/lib/legal";

export const metadata: Metadata = { title: "Privacy Policy — FinPilot" };

export default function PrivacyPage() {
  const { product, operator, privacyEmail, grievanceOfficer } = LEGAL;
  return (
    <LegalPage title="Privacy Policy">
      <p>
        {product} is operated by {operator} (&ldquo;we&rdquo;, &ldquo;us&rdquo;). This policy explains what personal
        data we process when you use {product}, why, who else sees it, how long we keep it, and the rights you
        have under India&rsquo;s Digital Personal Data Protection Act, 2023 and the rules made under it. We process
        your personal data on the basis of the consent you give when you create an account.
      </p>

      <H2>1. What we collect</H2>
      <List>
        <li><b>Account details</b> — your name, email address and a one-way hash of your password (never the password itself), and the date and version of the consent you gave.</li>
        <li><b>Documents you upload</b> — bank and card statements, bills, invoices, order and alert emails, and spreadsheets. These contain financial information such as transactions, balances, merchants and the names of people you paid or were paid by.</li>
        <li><b>What we derive from them</b> — a cleaned list of your transactions, your bills, spending categories and habits (&ldquo;segments&rdquo;), charts, and any reports you generate.</li>
        <li><b>Questions you ask</b> FinPilot&rsquo;s assistant, and its answers, while you use the chat.</li>
        <li><b>Payment records</b> if you buy Pro — plan, amount, date and order reference. Card, UPI and bank details are entered with our payment processor, Razorpay; we never see or store them.</li>
        <li><b>Technical logs</b> — IP address, time, and the action taken (for example a sign-in or an upload), used for security.</li>
      </List>

      <H2>2. How we protect what&rsquo;s in your documents</H2>
      <p>
        Before anything is stored, account numbers, card numbers, UPI IDs, phone numbers, customer/consumer IDs
        and email addresses inside your documents are masked (for example <code>XXXXXX7890</code>). Passwords for
        protected PDFs are used once to open the file and are never stored. Your data is kept separate from every
        other user&rsquo;s and can only be reached after you sign in.
      </p>

      <H2>3. Why we use it</H2>
      <List>
        <li>To turn your documents into one ledger, and to show your dashboard, habits, bills and charts.</li>
        <li>To answer your questions and write reports, using an AI model (see section 4).</li>
        <li>To run your account, enforce plan limits and process payments.</li>
        <li>To keep the service secure, prevent abuse, and meet legal obligations.</li>
      </List>
      <p>We do not sell your data, use it for advertising, or share it with anyone for their own purposes.</p>

      <H2>4. Who else processes it</H2>
      <List>
        <li><b>Google (Gemini API)</b> — when you ask a question or generate a report, short masked extracts of your documents and transaction descriptions (which can include merchant names and the names of people you paid) are sent to Google&rsquo;s Gemini model to produce the answer. We use the paid Gemini API, under which Google does not use this data to train its models. Google may process it outside India.</li>
        <li><b>Our hosting provider</b> — stores the service and its database. [Provider name and region]</li>
        <li><b>Razorpay</b> — processes payments for Pro.</li>
        <li><b>Our email provider</b> — sends password-reset emails. [Provider name]</li>
      </List>
      <p>Each processes data only on our instructions and to provide the service. We may disclose data where the law requires it.</p>

      <H2>5. How long we keep it</H2>
      <List>
        <li><b>Each upload</b> — the original files, your transactions, bills, habits, charts, reports and search index — is kept for {LEGAL.uploadRetentionDays} days after its analysis and then permanently deleted. Right after the analysis you can choose to delete it immediately instead. Pro users can extend an upload by {LEGAL.uploadRetentionDays} days at a time. We email you 7 days and 1 day before the deletion, and confirm when it is done.</li>
        <li><b>Security logs</b> — {LEGAL.logRetentionDays} days, as required for incident reporting in India.</li>
        <li><b>Payment records</b> — for as long as tax and accounting law requires, even after you delete your account. They contain no documents, only the order, amount and date.</li>
      </List>

      <H2>6. Your rights</H2>
      <p>You can, at any time:</p>
      <List>
        <li><b>Access and take your data</b> — <i>Account → Download my data</i> gives you everything we hold, as files.</li>
        <li><b>Correct</b> your details, or ask us to correct anything inaccurate.</li>
        <li><b>Erase</b> any upload, or your whole account — <i>Account → Delete my account</i> removes your account, every upload, your ledger and reports immediately. Payment records are kept as described above.</li>
        <li><b>Withdraw consent</b> — deleting your account withdraws it; we then stop processing your data.</li>
        <li><b>Raise a grievance</b> with our Grievance Officer (section 9), and, if unresolved, with the Data Protection Board of India.</li>
        <li><b>Nominate</b> someone to exercise these rights on your behalf in the event of death or incapacity, by writing to {privacyEmail}.</li>
      </List>

      <H2>7. Security and breaches</H2>
      <p>
        We use encrypted connections (HTTPS), hashed passwords, per-user isolation, sign-in rate limiting and
        encrypted server disks, and we test the service against common attacks. If a personal-data breach
        affects you, we will tell you and the Data Protection Board of India without delay, and report it to
        CERT-In as the law requires.
      </p>

      <H2>8. Children</H2>
      <p>{product} is only for people aged 18 or over. We do not knowingly process children&rsquo;s data; if we learn we have, we delete it.</p>

      <H2>9. Contact and grievances</H2>
      <p>
        Privacy questions: {privacyEmail}. Grievance Officer: {grievanceOfficer.name}, {grievanceOfficer.email}.
        We acknowledge grievances within 48 hours and aim to resolve them within 30 days. See also our{" "}
        <a href="/contact" className="underline">Contact &amp; Grievances</a> page.
      </p>

      <H2>10. Changes</H2>
      <p>
        If we change this policy materially we will tell you in the app and, where the law requires, ask for your
        consent again before the change applies to you.
      </p>
    </LegalPage>
  );
}
