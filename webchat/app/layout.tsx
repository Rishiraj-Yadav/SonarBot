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
        <div className="mx-auto min-h-screen max-w-[1500px] px-4 py-6 sm:px-6">{children}</div>
      </body>
    </html>
  );
}
