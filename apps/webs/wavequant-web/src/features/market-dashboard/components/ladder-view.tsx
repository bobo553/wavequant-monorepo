"use client";

import { type JSX, useState } from "react";

import { Button, Card, CardContent } from "@repo/design-system-web/components";

import { LadderBoard } from "@/features/market-dashboard/components/ladder-board";
import { LimitUpTable, limitUpCsv } from "@/features/market-dashboard/components/limit-up-table";
import { MarketPanel } from "@/features/market-dashboard/components/market-panel";
import { chinaToday, shiftCalendarDate } from "@/features/market-dashboard/limit-up-api";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";
import { useLimitUpLadder } from "@/features/market-dashboard/use-limit-up-ladder";

/** Daily data is replaced immediately on date changes, including failed requests. */
export function LadderView({ compact = false }: { compact?: boolean }): JSX.Element {
    const workspace = useMarketWorkspace();
    const { data, error, loading, refresh } = useLimitUpLadder(workspace.ladderDate);
    const [search, setSearch] = useState("");
    const selected = workspace.ladderDate || data?.date || "";
    const today = chinaToday();
    const rows = (data?.stocks ?? []).filter((stock) =>
        `${stock.code} ${stock.name} ${stock.industry}`.includes(search.trim()),
    );
    const height = Math.max(0, ...rows.map((stock) => stock.streak));
    return (
        <div className="flex min-w-0 flex-col gap-3" aria-label="每日涨停天梯">
            {!compact && <h2 className="text-lg font-bold">每日涨停天梯</h2>}
            <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs">
                    查询日期
                    <input
                        aria-label="涨停天梯日期"
                        type="date"
                        min="1990-01-01"
                        max={today}
                        value={selected}
                        onChange={(event) => {
                            if (event.target.value) workspace.setLadderDate(event.target.value);
                        }}
                        className="border-border bg-background h-9 min-w-0 rounded-md border px-2"
                    />
                </label>
                <Button
                    variant="outline"
                    size="sm"
                    disabled={!selected || selected <= "1990-01-01"}
                    onClick={() => workspace.setLadderDate(shiftCalendarDate(selected, -1))}
                >
                    前一天
                </Button>
                <Button
                    variant="outline"
                    size="sm"
                    disabled={!selected || selected >= today}
                    onClick={() => workspace.setLadderDate(shiftCalendarDate(selected, 1))}
                >
                    后一天
                </Button>
                <Button variant="outline" size="sm" onClick={() => workspace.setLadderDate(today)}>
                    今天
                </Button>
                <Button variant="outline" size="sm" disabled={loading} onClick={refresh}>
                    刷新
                </Button>
            </div>
            <p className="text-muted-foreground text-xs">AkShare · 东方财富涨停股池 · 按自然日切换，休市日可能无数据</p>
            {loading && (
                <p role="status" className="py-8 text-center text-sm">
                    正在读取{selected || "今日"}涨停池…
                </p>
            )}
            {error && (
                <div role="alert" className="rounded-md border border-amber-400/40 p-4 text-sm">
                    {error}
                    <Button variant="outline" size="sm" className="ml-2" onClick={refresh}>
                        重试
                    </Button>
                </div>
            )}
            {data && (
                <>
                    <p className="text-muted-foreground text-xs">
                        数据日期 {data.date} · 抓取时间{" "}
                        {new Date(data.fetched_at).toLocaleString("zh-CN", {
                            timeZone: "Asia/Shanghai",
                            hour12: false,
                        })}
                        （北京时间）
                    </p>
                    <p className="text-muted-foreground text-xs">{data.notice}</p>
                    {data.status === "empty" ? (
                        <p role="status" className="py-8 text-center">
                            {data.date} 暂无可展示的涨停股票
                        </p>
                    ) : (
                        <>
                            {!compact && (
                                <>
                                    <input
                                        aria-label="筛选涨停股票"
                                        value={search}
                                        onChange={(event) => setSearch(event.target.value)}
                                        placeholder="搜索代码、名称或行业"
                                        className="border-border bg-background h-9 rounded-md border px-3 text-sm"
                                    />
                                    <section aria-label="涨停统计" className="grid grid-cols-3 gap-2">
                                        {[
                                            ["涨停家数", rows.length],
                                            ["连板家数", rows.filter((stock) => stock.streak > 1).length],
                                            ["最高连板", height],
                                        ].map(([label, value]) => (
                                            <Card key={label} className="rounded-lg">
                                                <CardContent className="p-3">
                                                    <span className="text-muted-foreground text-xs">{label}</span>
                                                    <strong className="text-primary mt-1 block text-xl">{value}</strong>
                                                </CardContent>
                                            </Card>
                                        ))}
                                    </section>
                                </>
                            )}
                            {rows.length === 0 ? (
                                <p role="status">没有匹配的涨停股票</p>
                            ) : (
                                <LadderBoard stocks={rows} date={data.date} compact={compact} />
                            )}
                            {!compact && (
                                <MarketPanel
                                    title={`涨停明细 · ${rows.length} 只`}
                                    action={
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={!rows.length}
                                            onClick={() =>
                                                workspace.downloadCsv(
                                                    `wavequant-limit-up-${data.date}.csv`,
                                                    limitUpCsv(data.date, rows),
                                                )
                                            }
                                        >
                                            导出当日明细
                                        </Button>
                                    }
                                    contentClassName="p-0"
                                >
                                    <LimitUpTable stocks={rows} date={data.date} />
                                </MarketPanel>
                            )}
                        </>
                    )}
                </>
            )}
        </div>
    );
}
