"use client";

import { type JSX, useState } from "react";

import { Button, Card, CardContent, CardHeader } from "@repo/design-system-web/components";

import { LadderBoard } from "@/features/market-dashboard/components/ladder-board";
import { MarketPanel } from "@/features/market-dashboard/components/market-panel";
import { StockTable } from "@/features/market-dashboard/components/stock-table";
import { stockQuotes } from "@/features/market-dashboard/market-data";
import type { TStockPool } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const advancement = [
    { label: "1 进 2", value: "50.00%", detail: "3 / 6 只 · 盘中暂定" },
    { label: "2 进 3", value: "—", detail: "0 / 0 只 · 盘中暂定" },
    { label: "3 进 4", value: "42.86%", detail: "3 / 7 只 · 盘中暂定" },
    { label: "4 进 5（昨 4 板）", value: "—", detail: "0 / 0 只 · 盘中暂定" },
];

const stockPools: Array<{ id: TStockPool; label: string }> = [
    { id: "sealed", label: "当前封板" },
    { id: "broken", label: "炸板未封" },
    { id: "touched", label: "曾经触板" },
    { id: "yesterday", label: "昨日涨停" },
    { id: "down", label: "当前跌停" },
];

/** 展示晋级率、高度梯队与可核验涨停股票池。 */
export function LadderView(): JSX.Element {
    const workspace = useMarketWorkspace();
    const [pool, setPool] = useState<TStockPool>("sealed");
    const rows =
        pool === "sealed"
            ? stockQuotes
            : pool === "broken"
              ? stockQuotes
                    .filter((stock) => stock.openCount > 0)
                    .map((stock) => ({ ...stock, status: "炸板未回封", change: "+6.18%" }))
              : pool === "touched"
                ? stockQuotes.slice(0, 12)
                : pool === "yesterday"
                  ? [...stockQuotes]
                        .reverse()
                        .slice(0, 8)
                        .map((stock) => ({ ...stock, status: "昨日固定队列" }))
                  : stockQuotes.slice(0, 4).map((stock, index) => ({
                        ...stock,
                        code: `SIM0${50 + index}`,
                        name: ["远桥材料", "北陆新材", "原野科技", "恒川制造"][index] ?? stock.name,
                        change: stock.board === "北交所" ? "-29.99%" : "-10.00%",
                        status: "当前跌停",
                    }));
    return (
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">涨停阶梯 · 强弱接力</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        同一日内状态与收盘确认分开；10%、20%、30% 样本请用顶部市场筛选分开比较。
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                        workspace.openInfo(
                            "连板、炸板与晋级口径",
                            "当前封板、曾触板、炸板未回封互斥计算。晋级分母固定为昨日恰处于对应梯队的队列，不根据今日结果重选成员。盘中连板为暂定，15:00 才确认。",
                        )
                    }
                >
                    连板 / 炸板率 / 晋级率
                </Button>
            </div>
            <section aria-label="晋级率" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {advancement.map((item) => (
                    <Card key={item.label} className="rounded-lg">
                        <CardHeader className="text-muted-foreground px-4 pt-3 pb-1 text-xs">{item.label}</CardHeader>
                        <CardContent className="px-4 pb-3">
                            <strong className="text-primary text-xl">{item.value}</strong>
                            <p className="text-muted-foreground mt-1 text-[9px]">{item.detail}</p>
                        </CardContent>
                    </Card>
                ))}
            </section>
            <section className="grid gap-2 md:grid-cols-3">
                <Card className="rounded-lg">
                    <CardContent className="p-4">
                        <span className="text-muted-foreground text-xs">昨日涨停表现</span>
                        <strong className="mt-3 block text-xl text-rose-400">+11.17%</strong>
                        <small className="text-muted-foreground mt-2 block text-[9px]">
                            昨收 → 当前价 · 有效 14/14 只 · 等权
                        </small>
                    </CardContent>
                </Card>
                <Card className="rounded-lg">
                    <CardContent className="p-4">
                        <span className="text-muted-foreground text-xs">昨日连板表现</span>
                        <strong className="mt-3 block text-xl text-rose-400">+8.65%</strong>
                        <small className="text-muted-foreground mt-2 block text-[9px]">
                            昨收 → 当前价 · 有效 8/8 只 · 等权
                        </small>
                    </CardContent>
                </Card>
                <Card className="rounded-lg">
                    <CardContent className="p-4">
                        <strong className="text-xs">先固定昨日队列，再看今天表现</strong>
                        <p className="text-muted-foreground mt-2 text-[9px] leading-4">
                            这是价格表现，不是开盘买入收益；剔除及缺失有明细。
                        </p>
                    </CardContent>
                </Card>
            </section>
            <div className="rounded-md border border-amber-400/25 bg-amber-400/8 px-3 py-2 text-[10px] text-amber-300">
                盘中观察：卡片的“暂定连板”不代表已经收盘涨停。晋级分母固定为前一日对应梯队；缺失数据在明细中单列。
            </div>
            <MarketPanel title="高度阶梯" contentClassName="px-3 pb-1">
                <LadderBoard />
            </MarketPanel>
            <MarketPanel
                title="涨跌停股票池"
                action={
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                            workspace.downloadCsv(`wavequant-${pool}-${workspace.date}.csv`, [
                                ["代码", "名称", "现价", "涨跌幅", "状态", "首封", "开板次数"],
                                ...rows.map((stock) => [
                                    stock.code,
                                    stock.name,
                                    stock.price,
                                    stock.change,
                                    stock.status,
                                    stock.firstSeal,
                                    stock.openCount,
                                ]),
                            ])
                        }
                    >
                        导出此池
                    </Button>
                }
                contentClassName="p-0"
            >
                <div className="border-border flex flex-wrap items-center gap-1 border-b p-3">
                    {stockPools.map((item) => (
                        <Button
                            key={item.id}
                            size="sm"
                            variant={pool === item.id ? "secondary" : "outline"}
                            className={pool === item.id ? "text-primary" : ""}
                            onClick={() => setPool(item.id)}
                        >
                            {item.label}
                        </Button>
                    ))}
                    <span className="text-muted-foreground ml-auto text-[10px]">{rows.length} 只</span>
                </div>
                <StockTable rows={rows} />
                <p className="border-border text-muted-foreground border-t px-3 py-2 text-[9px]">
                    当前范围炸板率：4 只炸板未回封 ÷ 18 只曾触板 = 22.22%。不同股票池不改变统计分母。
                </p>
            </MarketPanel>
        </div>
    );
}
