import type { Metadata } from "next";

import { H2, LegalPage } from "@/components/legal/legal-page";
import { LEGAL } from "@/lib/legal";

export const metadata: Metadata = { title: "Contact & Grievances — FinPilot" };

export default function ContactPage() {
  const { operator, address, supportEmail, privacyEmail, grievanceOfficer } = LEGAL;
  return (
    <LegalPage title="Contact & Grievances">
      <H2>Support</H2>
      <p>
        Questions about your account, uploads, plans or payments: <b>{supportEmail}</b>. We reply within two
        working days.
      </p>

      <H2>Privacy</H2>
      <p>
        Questions about your data, or requests to access, correct or erase it: <b>{privacyEmail}</b>. Most requests
        can be done instantly in the app under <i>Account</i>.
      </p>

      <H2>Grievance Officer</H2>
      <p>
        If you have a complaint about how we handle your data or about the service, contact our Grievance Officer:
      </p>
      <p className="rounded-lg border bg-muted/30 p-4">
        <b>{grievanceOfficer.name}</b>
        <br />
        {grievanceOfficer.email}
        <br />
        {operator}, {address}
      </p>
      <p>
        We acknowledge every grievance within 48 hours and aim to resolve it within 30 days. If you are not
        satisfied with our response, you may complain to the Data Protection Board of India.
      </p>
    </LegalPage>
  );
}
