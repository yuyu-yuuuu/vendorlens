import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "VendorLens · 供应商评审",
  description: "项目资料、供应商评审与原文证据核验工作台",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
