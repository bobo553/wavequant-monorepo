"use client";

import type { JSX } from "react";

import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@repo/design-system-web/components";

/** 捕获看盘页面渲染异常并提供原地重试。 */
export default function ErrorPage({ reset }: { reset: () => void }): JSX.Element {
    return (
        <main className="bg-background grid min-h-screen place-items-center p-6">
            <Card className="max-w-md">
                <CardHeader>
                    <CardTitle>看盘页面暂时不可用</CardTitle>
                    <CardDescription>本地页面渲染失败，当前没有发出任何交易请求。</CardDescription>
                </CardHeader>
                <CardContent>
                    <Button onClick={reset}>重新加载页面</Button>
                </CardContent>
            </Card>
        </main>
    );
}
