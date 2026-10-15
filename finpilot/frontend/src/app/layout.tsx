import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "FinPilot — your personal expense guide",
  description:
    "Turn bank statements, card statements, bills, receipts, emails and spreadsheets into one clean ledger.",
};

/**
 * Dark is the default. A saved choice ("finpilot.theme" = "light") is applied
 * before first paint, so a light-mode user never sees a dark flash (and vice
 * versa). The class is the theme mechanism (Tailwind's `dark` variant).
 */
const themeScript = `try{if(localStorage.getItem("finpilot.theme")==="light")document.documentElement.classList.remove("dark")}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full bg-background font-sans">{children}</body>
    </html>
  );
}
