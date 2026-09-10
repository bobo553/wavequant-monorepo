"use client";

import type { JSX } from "react";

import { Button, Card, CardContent } from "@repo/design-system-web/components";
import { IconAlertTriangle } from "@tabler/icons-react";

/** 捕获后台路由异常并提供原地恢复入口。 */
export default function DashboardError({
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}): JSX.Element {
    return (
        <Card>
            <CardContent className="flex min-h-96 flex-col items-center justify-center px-6 text-center">
                <span className="bg-destructive/10 text-destructive mb-4 grid size-12 place-items-center rounded-xl">
                    <IconAlertTriangle aria-hidden="true" />
                </span>
                <h1 className="text-xl font-bold">页面加载失败</h1>
                <p className="text-muted-foreground mt-2 max-w-md text-sm">
                    请检查网络连接或稍后重试。如果问题持续出现，请联系系统管理员。
                </p>
                <Button className="mt-5" onClick={reset}>
                    重新加载
                </Button>
            </CardContent>
        </Card>
    );
}
