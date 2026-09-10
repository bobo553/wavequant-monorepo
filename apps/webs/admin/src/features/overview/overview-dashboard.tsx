import type { JSX } from "react";

import { PageHeader } from "@/shared/components/page-header/page-header";
import { Button } from "@repo/design-system-web/components";
import { IconCalendar, IconDownload } from "@tabler/icons-react";

import { DashboardChart } from "@/features/overview/components/dashboard-chart";
import { MetricCard } from "@/features/overview/components/metric-card";
import { RecentOrders } from "@/features/overview/components/recent-orders";
import { channelShare, metrics, recentOrders, revenueTrend } from "@/features/overview/overview-data";

/** 组合经营指标、趋势、渠道结构和订单明细的后台首页。 */
export function OverviewDashboard(): JSX.Element {
    return (
        <div className="space-y-6">
            <PageHeader
                eyebrow="Overview"
                title="经营概览"
                description="聚合关键业务指标，快速识别增长机会与经营风险。"
                actions={
                    <>
                        <Button variant="outline">
                            <IconCalendar aria-hidden="true" />
                            最近 30 天
                        </Button>
                        <Button>
                            <IconDownload aria-hidden="true" />
                            导出报表
                        </Button>
                    </>
                }
            />
            <section aria-label="关键经营指标" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                {metrics.map((metric) => (
                    <MetricCard key={metric.label} {...metric} />
                ))}
            </section>
            <section aria-label="经营趋势图表" className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,1fr)]">
                <DashboardChart
                    type="line"
                    title="营收趋势"
                    description="近 6 个月确认收入，单位：千元。"
                    data={revenueTrend}
                />
                <DashboardChart
                    type="pie"
                    title="渠道贡献"
                    description="本月收入按获客渠道的贡献占比。"
                    data={channelShare}
                />
            </section>
            <RecentOrders orders={recentOrders} />
        </div>
    );
}
