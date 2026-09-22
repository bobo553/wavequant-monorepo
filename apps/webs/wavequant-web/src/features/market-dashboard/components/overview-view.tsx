"use client";

import type { JSX } from "react";

import dynamic from "next/dynamic";

import { Badge, Button, Card, CardContent, CardHeader, Skeleton } from "@repo/design-system-web/components";
import { IconArrowRight, IconChevronRight, IconInfoCircle } from "@tabler/icons-react";

import { BreadthBars } from "@/features/market-dashboard/components/breadth-bars";
import { LadderView } from "@/features/market-dashboard/components/ladder-view";
import { MarketPanel } from "@/features/market-dashboard/components/market-panel";
import { StockTable } from "@/features/market-dashboard/components/stock-table";
import { marketEvents, marketMetrics, sectorHeat, stockQuotes } from "@/features/market-dashboard/market-data";
import type { TMarketTabId } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const MarketBreadthChart = dynamic(
    () =>
        import("@/features/market-dashboard/components/market-breadth-chart").then(
            (module) => module.MarketBreadthChart,
        ),
    { ssr: false, loading: () => <Skeleton className="h-48 w-full" /> },
);

interface IOverviewViewProps {
    onNavigate: (tab: TMarketTabId) => void;
}

const metricTone = {
    amber: "text-amber-300",
    muted: "text-slate-300",
    positive: "text-emerald-400",
    up: "text-rose-400",
} as const;

/** 组合市场宽度、板块热度、阶梯、异动和行情排行。 */
export function OverviewView({ onNavigate }: IOverviewViewProps): JSX.Element {
    const workspace = useMarketWorkspace();
    return (
        <div className="flex flex-col gap-3">
            <section aria-label="市场关键指标" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
                {marketMetrics.map((metric) => (
                    <Card
                        key={metric.label}
                        role="button"
                        tabIndex={0}
                        onClick={() =>
                            workspace.openInfo(
                                `${metric.label} · 统计口径`,
                                `${metric.value}${metric.suffix ?? ""}\n\n${metric.detail}。统计使用当前范围、同一观察时点和有效样本分母；缺失值不以 0 填充。`,
                            )
                        }
                        onKeyDown={(event) => {
                            if (event.key === "Enter") workspace.openInfo(`${metric.label} · 统计口径`, metric.detail);
                        }}
                        className="hover:border-primary/45 rounded-lg"
                    >
                        <CardHeader className="flex-row items-center justify-between px-4 pt-3 pb-1">
                            <span className="text-muted-foreground text-xs">{metric.label}</span>
                            <IconChevronRight aria-hidden="true" className="text-muted-foreground" />
                        </CardHeader>
                        <CardContent className="px-4 pb-3">
                            <strong className={`text-2xl tracking-tight tabular-nums ${metricTone[metric.tone]}`}>
                                {metric.value}
                            </strong>
                            {metric.suffix ? (
                                <span className="text-muted-foreground ml-1 text-sm">{metric.suffix}</span>
                            ) : null}
                            <p className="text-muted-foreground mt-1 text-[9px]">{metric.detail}</p>
                        </CardContent>
                    </Card>
                ))}
            </section>

            <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_315px]">
                <div className="flex min-w-0 flex-col gap-3">
                    <MarketPanel
                        title="大盘与市场广度"
                        action={
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                    workspace.openInfo(
                                        "市场广度统计口径",
                                        "上涨、下跌和平盘按当前价相对昨收计算；停牌、缺失和无成交样本单独排除。涨跌幅分布可点击查看分桶组成。成交额与前一交易日同一时刻比较。",
                                    )
                                }
                            >
                                统计口径
                                <IconInfoCircle data-icon="inline-end" aria-hidden="true" />
                            </Button>
                        }
                    >
                        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_270px]">
                            <div className="min-w-0">
                                <div className="mb-1 flex items-baseline gap-3">
                                    <strong className="text-2xl text-rose-400">+6.04%</strong>
                                    <span className="text-muted-foreground text-[10px]">当前范围 · 成分等权</span>
                                </div>
                                <MarketBreadthChart />
                                <div className="text-muted-foreground flex gap-4 text-[9px]">
                                    <span>
                                        <i className="bg-primary mr-1 inline-block h-0.5 w-3" />
                                        当前范围（与基准一致）
                                    </span>
                                    <span>同一时间轴 · 鼠标悬停联动</span>
                                </div>
                            </div>
                            <BreadthBars />
                        </div>
                    </MarketPanel>

                    <div className="grid gap-3 lg:grid-cols-2">
                        <MarketPanel
                            title="板块热力"
                            action={
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => onNavigate("sectors")}
                                    className="text-primary"
                                >
                                    轮动分析
                                    <IconArrowRight data-icon="inline-end" aria-hidden="true" />
                                </Button>
                            }
                            contentClassName="grid grid-cols-2 gap-1 p-3"
                        >
                            {sectorHeat.map((sector, index) => (
                                <button
                                    key={sector.name}
                                    type="button"
                                    onClick={() => {
                                        workspace.setSelectedSector(sector.name);
                                        onNavigate("sectors");
                                    }}
                                    className={`rounded-md border border-rose-400/10 p-3 text-left transition-colors hover:border-rose-400/35 ${index < 4 ? "bg-rose-400/20" : "bg-rose-400/12"}`}
                                >
                                    <strong className="block text-xs">{sector.name}</strong>
                                    <b className="mt-1 block text-lg text-rose-400">{sector.change}</b>
                                    <span className="text-muted-foreground text-[9px]">{sector.detail}</span>
                                </button>
                            ))}
                        </MarketPanel>
                        <MarketPanel
                            title="涨停阶梯"
                            action={
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => onNavigate("ladder")}
                                    className="text-primary"
                                >
                                    展开
                                    <IconArrowRight data-icon="inline-end" aria-hidden="true" />
                                </Button>
                            }
                            contentClassName="px-3 pb-1"
                        >
                            <LadderView compact />
                        </MarketPanel>
                    </div>

                    <MarketPanel
                        title="行情排行"
                        action={
                            <span className="text-muted-foreground text-[10px]">
                                当前范围 · 涨幅前 10
                                <Button variant="outline" size="sm" onClick={() => onNavigate("ladder")}>
                                    完整行情
                                </Button>
                            </span>
                        }
                        contentClassName="p-0"
                    >
                        <StockTable rows={stockQuotes.slice(0, 10)} />
                    </MarketPanel>
                </div>

                <aside className="flex flex-col gap-3">
                    <MarketPanel
                        title="盘中异动"
                        action={
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => onNavigate("radar")}
                                className="text-primary"
                            >
                                全部
                            </Button>
                        }
                        contentClassName="p-0"
                    >
                        <div className="divide-border divide-y">
                            {marketEvents.map((event) => (
                                <button
                                    key={`${event.time}-${event.name}`}
                                    type="button"
                                    onClick={() =>
                                        workspace.openStock(
                                            stockQuotes.find((stock) => stock.name === event.name)?.code ?? "SIM001",
                                        )
                                    }
                                    className="hover:bg-background/35 grid w-full grid-cols-[40px_minmax(0,1fr)_16px] items-start gap-2 px-3 py-3 text-left"
                                >
                                    <time className="text-muted-foreground pt-0.5 text-[10px]">{event.time}</time>
                                    <span>
                                        <span className="flex items-center gap-2">
                                            <Badge className="rounded bg-rose-500/15 text-[9px] text-rose-300">
                                                {event.category}
                                            </Badge>
                                            <strong className="text-[11px]">{event.name}</strong>
                                        </span>
                                        <small className="text-muted-foreground mt-1 block text-[9px]">
                                            {event.detail}
                                        </small>
                                    </span>
                                    <IconChevronRight aria-hidden="true" className="text-muted-foreground mt-1" />
                                </button>
                            ))}
                        </div>
                    </MarketPanel>
                    <MarketPanel title="主线观察">
                        <p className="text-muted-foreground text-[10px]">全板块对照 · 等权涨幅第一 · 非买入推荐</p>
                        <button
                            type="button"
                            onClick={() => {
                                workspace.setSelectedSector("人形机器人");
                                onNavigate("sectors");
                            }}
                            className="mt-4 flex items-center gap-2 text-lg font-semibold"
                        >
                            人形机器人 <IconArrowRight aria-hidden="true" />
                        </button>
                        <div className="mt-5 grid grid-cols-2 gap-3">
                            <div>
                                <b className="block text-2xl text-rose-400">+7.24%</b>
                                <span className="text-muted-foreground text-[9px]">成分等权</span>
                            </div>
                            <div>
                                <b className="block text-2xl">3/5</b>
                                <span className="text-muted-foreground text-[9px]">封板 / 有效成分</span>
                            </div>
                        </div>
                        <p className="text-muted-foreground mt-4 text-[10px] leading-5">
                            看扩散是否持续，也看炸板与未兑现风险。不把热度当成确定性。
                        </p>
                        <Button variant="outline" className="mt-4 w-full" onClick={() => onNavigate("themes")}>
                            查看催化与反向证据
                        </Button>
                    </MarketPanel>
                </aside>
            </div>
        </div>
    );
}
