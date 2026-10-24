"use client";

import { useEffect } from "react";
import { LoadController } from "@repo/async-ui";

type TFeatureWindow = Window & {
    wavequantCreateFeatureLoader?: <Value>(loader: () => Promise<Value>) => LoadController<Value>;
};

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
        const runtimeWindow: TFeatureWindow = window;
        runtimeWindow.wavequantCreateFeatureLoader = <Value,>(loader: () => Promise<Value>) =>
            new LoadController(loader, { timeoutMs: 8000, retry: 1 });
        performance.mark("wavequant:research-hydrated");
        const parsers = import("./research-parsers").then(({ installResearchParsers }) => {
            if (active) installResearchParsers();
        });
        // 缓存预览已独立显示；Schema与图表并行准备，均不阻塞React挂载。
        void parsers.then(() => {
            if (active) return ensureScript("wavequant-watchlist-bootstrap", "/watchlist-bootstrap.js", "module");
        }).catch((error: unknown) => {
            if (!active) return;
            const status = document.getElementById("watchlist-status");
            if (status) status.textContent = error instanceof Error ? error.message : String(error);
        });
        const charts = ensureScript("wavequant-lightweight-charts", "/vendor/lightweight-charts.js");
        void Promise.all([parsers, charts]).then(() => {
            if (!active) return;
            performance.mark("wavequant:research-runtime-start");
            return ensureScript("wavequant-research-runtime", "/app.js", "module");
        }).catch((error: unknown) => {
            if (!active) return;
            const errorPanel = document.getElementById("error");
            if (errorPanel) {
                errorPanel.hidden = false;
                errorPanel.textContent = error instanceof Error ? error.message : String(error);
            }
        });

        return () => {
            active = false;
        };
    }, []);

    return null;
}
