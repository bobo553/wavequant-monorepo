import { setTimeout } from "node:timers/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** 开发路由首次编译属于服务启动；只读取页面，不请求行情或启动研究任务。 */
export async function warmResearch({ origin, signal, timeoutMs = 60_000 }) {
    const deadline = Date.now() + timeoutMs;
    let lastError;
    while (Date.now() < deadline && !signal?.aborted) {
        try {
            const timeout = AbortSignal.timeout(Math.max(1, deadline - Date.now()));
            const response = await fetch(new URL("/research?page=workspace", origin), {
                signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
            });
            await response.arrayBuffer();
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return;
        } catch (error) {
            lastError = error;
            if (signal?.aborted || Date.now() >= deadline) break;
            await setTimeout(250, undefined, { signal });
        }
    }
    if (signal?.aborted) return;
    throw new Error(`Research page warmup failed: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
}

export function startResearchWarmup(child, origin) {
    const controller = new AbortController();
    const stop = () => controller.abort();
    child.once("exit", stop);
    child.once("error", stop);
    console.log("[wavequant-web] Preparing research page; waiting for first compilation.");
    void warmResearch({ origin, signal: controller.signal }).then(() => {
        if (!controller.signal.aborted) console.log(`[wavequant-web] Research page ready: ${origin}/research?page=workspace`);
    }).catch((error) => {
        if (!controller.signal.aborted) console.error(`[wavequant-web] ${error.message}`);
    }).finally(() => {
        child.removeListener("exit", stop);
        child.removeListener("error", stop);
    });
    return stop;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    await warmResearch({ origin: process.argv[2] || "http://127.0.0.1:3003" });
    console.log("[wavequant-web] Research page warmup complete.");
}
