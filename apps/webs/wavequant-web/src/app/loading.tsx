import type { JSX } from "react";

import { Skeleton } from "@repo/design-system-web/components";

/** 保持看盘首屏结构稳定的加载占位。 */
export default function Loading(): JSX.Element {
    return (
        <main className="bg-background grid min-h-screen gap-3 p-6">
            <Skeleton className="h-16 w-full" />
            <div className="grid grid-cols-3 gap-3">
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
            </div>
            <Skeleton className="h-[520px] w-full" />
        </main>
    );
}
