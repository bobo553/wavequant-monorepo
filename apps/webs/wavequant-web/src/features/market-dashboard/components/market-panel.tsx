import type { JSX, ReactNode } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";

interface IMarketPanelProps {
    action?: ReactNode;
    children: ReactNode;
    className?: string;
    contentClassName?: string;
    title: string;
}

/** 统一看盘模块的卡片标题、边框与内容间距。 */
export function MarketPanel({ action, children, className, contentClassName, title }: IMarketPanelProps): JSX.Element {
    return (
        <Card className={className}>
            <CardHeader className="border-border flex-row items-center justify-between gap-3 border-b px-4 py-3">
                <CardTitle className="text-sm">{title}</CardTitle>
                {action}
            </CardHeader>
            <CardContent className={contentClassName ?? "p-4"}>{children}</CardContent>
        </Card>
    );
}
