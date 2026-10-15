/**
 * The facts the legal pages need. Fill these in before launch, have a
 * lawyer review the pages, then set `draft: false` to remove the banner.
 */
export const LEGAL = {
  draft: true,
  product: "FinPilot",
  /** the legal entity or sole proprietor that operates FinPilot */
  operator: "[Your legal name or company]",
  address: "[Registered address, City, State, PIN]",
  supportEmail: "[support@yourdomain.com]",
  privacyEmail: "[privacy@yourdomain.com]",
  grievanceOfficer: {
    name: "[Grievance Officer name]",
    email: "[grievance@yourdomain.com]",
  },
  /** courts with jurisdiction over disputes */
  jurisdiction: "[City], India",
  effectiveDate: "15 October 2026",
  /** keep in step with CONSENT_VERSION / consent_version on the backend */
  version: "2026-10-15",
  /** every upload is erased this many days after its analysis (Pro can extend) */
  uploadRetentionDays: 30,
  logRetentionDays: 180,
  refundWindowDays: 7,
} as const;
