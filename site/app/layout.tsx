import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible, Big_Shoulders_Stencil, Martian_Mono } from "next/font/google";
import "./globals.css";

const display = Big_Shoulders_Stencil({ subsets: ["latin"], weight: ["700", "800", "900"], variable: "--font-display" });
const body = Atkinson_Hyperlegible({ subsets: ["latin"], weight: ["400", "700"], style: ["normal", "italic"], variable: "--font-body" });
const mono = Martian_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "MergeMind Field Manual",
  description:
    "Set up MergeMind, the GitHub agent that takes an issue from opened to merged fix, together with the contributor who found the bug.",
  icons: { icon: `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/icon.png` },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#dadcd3" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1818" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
