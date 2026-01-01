import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AutoBuySell",
  description: "Automated Algorithmic Trading",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <div className="min-h-screen bg-background text-foreground flex flex-col">
          <header className="border-b border-border p-4 bg-muted/20 flex justify-between items-center">
            <h1 className="text-xl font-bold">AutoBuySell Dashboard</h1>
            <nav className="space-x-4">
              <a href="/" className="hover:text-primary">Dashboard</a>
              <a href="/analysis" className="hover:text-primary">Analysis</a>
              <a href="/log" className="hover:text-primary">Logs</a>
            </nav>
          </header>
          <main className="flex-1 p-6">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
