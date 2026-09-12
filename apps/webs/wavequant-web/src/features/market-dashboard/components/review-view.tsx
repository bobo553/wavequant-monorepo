"use client";

import type { JSX } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";
import { IconDeviceFloppy, IconDownload } from "@tabler/icons-react";

import { marketEvents, marketMetrics, sectorHeat, stockQuotes } from "@/features/market-dashboard/market-data";
import { marketTimes, useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** 生成当前时点报告，保存人工笔记并导出 Markdown、JSON 和 CSV。 */
export function ReviewView(): JSX.Element {
    const workspace = useMarketWorkspace();
    const isClosed = workspace.timeIndex === marketTimes.length - 1;
    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">{isClosed ? "盘后复盘" : "盘中观察"} · 当时已知与后来新增分开</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        报告使用 {workspace.date} {workspace.time} 快照；人工笔记不参与统计或历史信号计算。
                    </p>
                </div>
                <Badge variant={isClosed ? "success" : "warning"}>
                    {isClosed ? "15:00 收盘确认" : "尚未收盘，不冒充收盘结论"}
                </Badge>
            </header>
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_380px]">
                <div className="space-y-3">
                    <Card className="rounded-lg">
                        <CardHeader>
                            <CardTitle>市场结构摘要</CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-3 sm:grid-cols-3">
                            {marketMetrics.slice(0, 6).map((metric) => (
                                <div
                                    key={metric.label}
                                    className="border-border bg-background/35 rounded-lg border p-3"
                                >
                                    <span className="text-muted-foreground text-[10px]">{metric.label}</span>
                                    <strong className="mt-1 block text-xl">
                                        {metric.value}
                                        {metric.suffix}
                                    </strong>
                                    <small className="text-muted-foreground">{metric.detail}</small>
                                </div>
                            ))}
                        </CardContent>
                    </Card>
                    <Card className="rounded-lg">
                        <CardHeader>
                            <CardTitle>主线、事件与次轮验证</CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-4 md:grid-cols-2">
                            <div>
                                <h3 className="text-sm font-semibold">板块变化</h3>
                                <ol className="mt-3 space-y-2">
                                    {sectorHeat.slice(0, 4).map((sector, index) => (
                                        <li key={sector.name} className="flex justify-between text-xs">
                                            <span>
                                                {index + 1}. {sector.name}
                                            </span>
                                            <b className="text-rose-400">{sector.change}</b>
                                        </li>
                                    ))}
                                </ol>
                            </div>
                            <div>
                                <h3 className="text-sm font-semibold">事件时间线</h3>
                                <ol className="mt-3 space-y-2">
                                    {marketEvents.slice(0, 4).map((event) => (
                                        <li key={`${event.time}-${event.name}`} className="text-xs">
                                            <time className="text-muted-foreground mr-2">{event.time}</time>
                                            {event.category} · {event.name}
                                        </li>
                                    ))}
                                </ol>
                            </div>
                            <div className="md:col-span-2">
                                <h3 className="text-sm font-semibold">次轮验证事项</h3>
                                <p className="text-muted-foreground mt-2 text-xs leading-6">
                                    核验人形机器人板块扩散是否持续；检查 4
                                    只炸板未回封样本；对高位暂定连板逐一记录开板与风险状态。以上是研究清单，不是仓位建议。
                                </p>
                            </div>
                        </CardContent>
                    </Card>
                </div>
                <Card className="rounded-lg">
                    <CardHeader>
                        <CardTitle>人工研究笔记</CardTitle>
                        <p className="text-muted-foreground text-[10px]">按样本日保存在浏览器，可跨盘中时点查看。</p>
                    </CardHeader>
                    <CardContent>
                        <label>
                            <span className="sr-only">人工研究笔记</span>
                            <textarea
                                value={workspace.note}
                                onChange={(event) => workspace.setNote(event.target.value)}
                                placeholder="记录当时的假设、反向证据与下次验证事项。请勿输入密码或交易密钥。"
                                className="border-border bg-background min-h-64 w-full resize-y rounded-lg border p-3 text-sm leading-7"
                            />
                        </label>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <Button onClick={workspace.saveNote}>
                                <IconDeviceFloppy data-icon="inline-start" aria-hidden="true" />
                                保存笔记
                            </Button>
                            <Button variant="outline" onClick={workspace.downloadMarkdown}>
                                <IconDownload data-icon="inline-start" aria-hidden="true" />
                                导出 Markdown
                            </Button>
                            <Button
                                variant="outline"
                                onClick={() => workspace.downloadJson(`wavequant-${workspace.date}-snapshot.json`)}
                            >
                                JSON
                            </Button>
                            <Button
                                variant="outline"
                                onClick={() =>
                                    workspace.downloadCsv(`wavequant-${workspace.date}-pool.csv`, [
                                        ["代码", "名称", "现价", "涨跌幅", "成交额", "换手率"],
                                        ...stockQuotes.map((stock) => [
                                            stock.code,
                                            stock.name,
                                            stock.price,
                                            stock.change,
                                            stock.amount,
                                            stock.turnover,
                                        ]),
                                    ])
                                }
                            >
                                CSV
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
