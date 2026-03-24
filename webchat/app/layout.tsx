import "./globals.css";
import type { ReactNode } from "react";
import { AppShell } from "../components/AppShell";

export const metadata = {
  title: "SonarBot WebChat",
  description: "Web control panel for SonarBot",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen px-4 py-4 sm:px-6">
          <AppShell>{children}</AppShell>
        </div>
      </body>
    </html>
  );
}
