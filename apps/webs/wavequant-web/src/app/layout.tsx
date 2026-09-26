import "./globals.css";

import type { JSX, ReactNode } from "react";

import type { Metadata } from "next";

import { WaveQuantShell } from "@/shared/components/wavequant-shell/wavequant-shell";

import "../../public/styles.css";

// 必须在首屏内容解析前执行，避免浅色偏好等待客户端脚本时先绘制深色页面。
const themeInitScript = `(() => {
    try {
        const payload = JSON.parse(localStorage.getItem("wavequant.market.v2") || "null");
        if (payload?.version !== 2 || !payload.state || typeof payload.state !== "object") return;
        const saved = payload.state;
        const light = saved.theme === "light";
        document.documentElement.classList.toggle("light", light);
        document.documentElement.classList.toggle("dark", !light);
        document.documentElement.dataset.theme = saved.colorTheme === "market-blue" ? "market-blue" : "wavequant-teal";
        document.documentElement.dataset.palette = saved.palette === "accessible" ? "accessible" : "classic";
        document.documentElement.dataset.density = saved.density === "compact" ? "compact" : "comfortable";
    } catch {
        // 存储不可用或数据损坏时保留服务端默认主题。
    }
})();`;

// Git 合并后刷新已打开的开发页面；发布版本不注入轮询。
const mainRevisionScript = `(() => {
    let revision;
    let reloading = false;
    let checking = false;
    async function check() {
        if (reloading || checking) return;
        checking = true;
        try {
            const response = await fetch("/dev-runtime-revision.json", { cache: "no-store" });
            if (!response.ok) return;
            const current = (await response.json()).revision;
            if (typeof current !== "string") return;
            if (revision && revision !== current) {
                reloading = true;
                window.setTimeout(() => window.location.reload(), 3000);
            }
            revision = current;
        } catch {
            // 服务重启时稍后再试。
        } finally {
            checking = false;
        }
    }
    check();
    window.setInterval(check, 2000);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) check();
    });
})();`;

export const metadata: Metadata = {
    title: "WaveQuant · 主控量化研究",
    description: "WaveQuant 本地日线研究、回测、证据复盘与全景市场工作台。",
};

/** WaveQuant 根布局，首屏绘制前恢复本地主题偏好。 */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>): JSX.Element {
    return (
        <html lang="zh-CN" className="dark" data-theme="wavequant-teal" suppressHydrationWarning>
            <head>
                <script id="wavequant-theme-init">{themeInitScript}</script>
                {process.env.NODE_ENV === "development" && (
                    <script id="wavequant-main-revision">{mainRevisionScript}</script>
                )}
            </head>
            <body className="min-h-screen antialiased">
                <WaveQuantShell>{children}</WaveQuantShell>
            </body>
        </html>
    );
}
