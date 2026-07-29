import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NASFlow — 把喜欢的内容带回家",
  description: "为 NAS 打造的开源媒体采集与自动整理中心。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
