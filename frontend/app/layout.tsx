import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import ClientShell from "@/components/ClientShell";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist-sans" });

export const metadata: Metadata = {
  title: "사업계획서 AI",
  description: "정부지원사업 사업계획서 AI 자동 작성 서비스",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" className={`${geist.variable} h-full`}>
      <body className="h-full antialiased">
        <ClientShell>{children}</ClientShell>
      </body>
    </html>
  );
}
