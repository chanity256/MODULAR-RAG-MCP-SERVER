import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import ThemeScript from "@/components/ThemeScript";
import { ThemeProvider } from "@/context/ThemeContext";

export const metadata: Metadata = {
  title: "Modular RAG",
  description: "Chat and dashboard for the Modular RAG MCP Server",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body className="font-sans antialiased">
        <ThemeProvider>
          <div className="flex h-screen overflow-hidden bg-slate-50 transition-colors duration-200 dark:bg-slate-900">
            <Sidebar />
            <main className="flex-1 overflow-y-auto">{children}</main>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
