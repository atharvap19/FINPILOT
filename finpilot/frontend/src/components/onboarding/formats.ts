/** What FinPilot can read. Shown on the landing page and in onboarding. */

import { FileSpreadsheet, FileText, Lock, Mail, Receipt, Table } from "lucide-react";

export const FORMATS = [
  {
    icon: FileSpreadsheet,
    title: "Bank statements",
    exts: [".csv", ".xlsx", ".xls"],
    body: "The download from net banking — header rows and totals are handled for you.",
  },
  {
    icon: FileText,
    title: "PDF statements",
    exts: [".pdf"],
    body: "Savings and credit-card statements with a text layer. Scans aren't supported.",
  },
  {
    icon: Lock,
    title: "Password-protected PDFs",
    exts: [".pdf"],
    body: "We ask for the password once and never store it.",
  },
  {
    icon: Receipt,
    title: "Bills & invoices",
    exts: [".pdf"],
    body: "Electricity, mobile, broadband — due dates are tracked and matched to payments.",
  },
  {
    icon: Mail,
    title: "Order & alert emails",
    exts: [".eml", ".mbox"],
    body: "Swiggy, Amazon, bank alerts — matched to the payment, with line items.",
  },
  {
    icon: Table,
    title: "Your own sheets",
    exts: [".xlsx", ".csv"],
    body: "Cash spends and budgets you track yourself.",
  },
] as const;
