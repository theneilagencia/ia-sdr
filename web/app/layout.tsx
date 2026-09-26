import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Sales Workforce",
  description: "Plataforma multiempresa de agentes comerciais de IA",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
