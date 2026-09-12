"use client";

import { useEffect } from "react";

function ensureScript(id: string, src: string, type?: "module"): Promise<void> {
    const current = document.getElementById(id) as HTMLScriptElement | null;
    if (current?.dataset.loaded === "true") return Promise.resolve();

    return new Promise((resolve, reject) => {
        const script = current ?? document.createElement("script");
        const handleLoad = (): void => {
            script.dataset.loaded = "true";
            resolve();
        };
        const handleError = (): void => reject(new Error(`无法加载研究工作台运行时：${src}`));

        script.addEventListener("load", handleLoad, { once: true });
        script.addEventListener("error", handleError, { once: true });
        if (!current) {
            script.id = id;
            script.src = src;
            if (type) script.type = type;
            document.body.append(script);
        }
    });
}

/**
 * 浏览器运行时边界。
 *
 * React 负责页面结构与组件生命周期；已验收的行情图表、筛选、扫描、回测和账本
 * 适配器按依赖顺序在 hydration 后挂载，避免 Next Script 对 module 脚本只预加载未执行。
 */
export function ResearchRuntime(): null {
    useEffect(() => {
        let active = true;

        void ensureScript("wavequant-lightweight-charts", "/vendor/lightweight-charts.js")
            .then(() => {
                if (!active) return;
                return ensureScript("wavequant-research-runtime", "/app.js", "module");
            })
            .catch((error: unknown) => {
                const message = error instanceof Error ? error.message : String(error);
                const errorPanel = document.getElementById("error");
                if (errorPanel) {
                    errorPanel.hidden = false;
                    errorPanel.textContent = message;
                }
            });

        return () => {
            active = false;
        };
    }, []);

    return null;
}
