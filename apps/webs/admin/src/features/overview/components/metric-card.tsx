import type { JSX } from "react";

import { Badge, Card, CardContent, CardHeader } from "@repo/design-system-web/components";
import { IconArrowDownRight, IconArrowUpRight } from "@tabler/icons-react";

import type { IMetricItem } from "@/features/overview/overview-data";

/** 展示单项经营指标及其环比变化。 */
export function MetricCard({ change, detail, direction, label, value }: IMetricItem): JSX.Element {
    const TrendIcon = direction === "up" ? IconArrowUpRight : IconArrowDownRight;

    return (
        <Card className="overflow-hidden">
            <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
                <p className="text-muted-foreground text-sm font-medium">{label}</p>
                <Badge variant="outline" className="gap-1">
                    <TrendIcon size={14} aria-hidden="true" />
                    {change}
                </Badge>
            </CardHeader>
            <CardContent>
                <p className="text-2xl font-bold tracking-tight">{value}</p>
                <p className="text-muted-foreground mt-2 text-xs">{detail}</p>
            </CardContent>
        </Card>
    );
}
