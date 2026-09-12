import "./globals.css";

import type { JSX, ReactNode } from "react";

import type { Metadata } from "next";
import Script from "next/script";

import { WaveQuantShell } from "@/shared/components/wavequant-shell/wavequant-shell";

import "../../public/styles.css";

export const metadata: Metadata = {
    title: "WaveQuant · 主控量化研究",
    description: "WaveQuant 本地日线研究、回测、证据复盘与全景市场工作台。",
};

/** WaveQuant 根布局，固定中文语义与深色量化工作台主题。 */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>): JSX.Element {
    return (
        <html lang="zh-CN" className="dark" data-theme="wavequant-teal" suppressHydrationWarning>
            <body className="min-h-screen antialiased">
                <Script id="wavequant-theme-init" src="/theme-init.js" strategy="beforeInteractive" />
                <WaveQuantShell>{children}</WaveQuantShell>
            </body>
        </html>
    );
}
