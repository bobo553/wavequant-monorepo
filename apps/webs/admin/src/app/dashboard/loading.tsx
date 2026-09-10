import type { JSX } from "react";

import { Card, Skeleton } from "@repo/design-system-web/components";

/** 在后台路由切换或服务端数据加载时保持页面布局稳定。 */
export default function DashboardLoading(): JSX.Element {
    return (
        <div aria-label="正在加载页面" aria-busy="true" className="space-y-6">
            <div className="space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-52" />
                <Skeleton className="h-5 w-full max-w-xl" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                {Array.from({ length: 4 }, (_, index) => (
                    <Skeleton key={index} className="h-32" />
                ))}
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                    <Skeleton className="m-5 h-80" />
                </Card>
                <Card>
                    <Skeleton className="m-5 h-80" />
                </Card>
            </div>
        </div>
    );
}
