"use client";

import { Component, type JSX, type ReactNode } from "react";
import type { AsyncViewFallbackProps } from "@repo/async-ui";

/** 异步内容保持占位尺寸；失败由用户明确重试，不阻塞相邻视图。 */
export function AsyncFeatureFallback({ status, retry }: AsyncViewFallbackProps): JSX.Element {
    return <div role={status === "failed" ? "alert" : "status"} className="panel min-h-48 rounded-lg border p-6" aria-busy={status !== "failed"}>
        <p>{status === "failed" ? "内容加载失败，请重试。" : "正在加载内容…"}</p>
        {status === "failed" ? <button type="button" onClick={() => { void retry().catch(() => undefined); }}>重新加载</button> : null}
    </div>;
}

/** 加载错误由async-ui处理；组件渲染异常限定在当前功能内。 */
export class AsyncFeatureBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    override state = { failed: false };
    static getDerivedStateFromError(): { failed: boolean } { return { failed: true }; }
    override render(): ReactNode {
        if (this.state.failed) return <div role="alert" className="panel min-h-48 p-6">
            <p>当前内容暂时无法显示。</p>
            <button type="button" onClick={() => this.setState({ failed: false })}>重试显示</button>
        </div>;
        return this.props.children;
    }
}
