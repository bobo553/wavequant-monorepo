"use client";
import type { JSX } from "react";

/** 错误反馈属于首屏兜底，使用轻量原生元素避免引入整组UI依赖。 */
export default function ErrorPage({ reset }: { reset: () => void }): JSX.Element {
    return <main className="bg-background grid min-h-screen place-items-center p-6">
        <section role="alert" className="border-border bg-card max-w-md rounded-xl border p-6 shadow-sm">
            <h1 className="text-lg font-semibold">看盘页面暂时不可用</h1>
            <p className="text-muted-foreground mt-2 text-sm">本地页面渲染失败，当前没有发出任何交易请求。</p>
            <button type="button" className="bg-primary text-primary-foreground mt-5 rounded-md px-4 py-2" onClick={reset}>重新加载页面</button>
        </section>
    </main>;
}
