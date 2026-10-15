import { LEGAL } from "@/lib/legal";

const LINKS = [
  { href: "/privacy", label: "Privacy Policy" },
  { href: "/terms", label: "Terms" },
  { href: "/refunds", label: "Refunds & Cancellation" },
  { href: "/contact", label: "Contact & Grievances" },
];

/** Legal links and the advice disclaimer, on every page. */
export function SiteFooter() {
  return (
    <footer className="border-t bg-background/60">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 px-6 py-6 text-xs text-muted-foreground">
        <nav className="flex flex-wrap gap-x-5 gap-y-1" aria-label="Legal">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href} className="hover:text-foreground hover:underline">
              {link.label}
            </a>
          ))}
        </nav>
        <p className="max-w-4xl leading-relaxed">
          {LEGAL.product} turns your own documents into a ledger and explains your spending. Its answers and
          reports are AI-generated and can contain mistakes; they are information, not financial, tax or
          investment advice. {LEGAL.product} is not a SEBI-registered investment adviser.
        </p>
        <p>
          © {new Date().getFullYear()} {LEGAL.operator}
        </p>
      </div>
    </footer>
  );
}
