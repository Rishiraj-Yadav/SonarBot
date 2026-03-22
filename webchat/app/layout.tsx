import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "SonarBot WebChat",
  description: "Web control panel for SonarBot",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto min-h-screen max-w-7xl p-6">{children}</div>
      </body>
    </html>
  );
}
