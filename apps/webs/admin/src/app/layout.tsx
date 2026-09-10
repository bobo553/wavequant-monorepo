import "./globals.css";

import type { JSX, ReactNode } from "react";

import type { Metadata } from "next";

import { ThemeProvider } from "@/providers/theme-provider";

export const metadata: Metadata = {
    title: {
        default: "Aster Admin",
        template: "%s | Aster Admin",
    },
    description: "基于 Next.js 的企业级管理后台模板。",
};

/** 管理后台根布局，统一主题与全局样式。 */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>): JSX.Element {
    return (
        <html lang="zh-CN" suppressHydrationWarning>
            <body className="min-h-screen antialiased">
                <ThemeProvider>{children}</ThemeProvider>
            </body>
        </html>
    );
}
