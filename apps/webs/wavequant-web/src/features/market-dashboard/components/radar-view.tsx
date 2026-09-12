"use client";

import { type JSX, useMemo, useState } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";
import { IconEye, IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react";

import { marketEvents, stockQuotes } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const eventTypes = ["全部", "首次封板", "炸板", "回封", "跌停", "量价异动"] as const;

/** 提供事件类型/观察组筛选、已读、暂停插入、恢复与时点定位。 */
export function RadarView(): JSX.Element {
    const workspace = useMarketWorkspace();
    const [eventType, setEventType] = useState<(typeof eventTypes)[number]>("全部");
    const [watchOnly, setWatchOnly] = useState(false);
    const events = useMemo(() => {
        const extended = [
            ...marketEvents,
            { time: "12:58", category: "炸板", name: "微澜光科", detail: "光电设备 · 已回封" },
            { time: "11:18", category: "量价异动", name: "云启算力", detail: "算力基础设施 · 成交额放大" },
            { time: "10:42", category: "跌停", name: "远桥材料", detail: "风险观察 · 当前跌停" },
        ];
        return extended.filter((event) => {
            if (eventType !== "全部" && event.category !== eventType) return false;
            if (!watchOnly) return true;
            const stock = stockQuotes.find((item) => item.name === event.name);
            return stock ? (workspace.watchGroups[stock.code]?.length ?? 0) > 0 : false;
        });
    }, [eventType, watchOnly, workspace.watchGroups]);
    const pendingCount = workspace.radarPaused ? 3 : 0;
    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">异动雷达 · 稳定事件时间线</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        暂停仅冻结新事件插入，不停止行情时钟；事件按 ID 去重。
                    </p>
                </div>
                <Button
                    variant={workspace.radarPaused ? "secondary" : "outline"}
                    onClick={() => workspace.setRadarPaused(!workspace.radarPaused)}
                >
                    {workspace.radarPaused ? (
                        <IconPlayerPlay data-icon="inline-start" aria-hidden="true" />
                    ) : (
                        <IconPlayerPause data-icon="inline-start" aria-hidden="true" />
                    )}
                    {workspace.radarPaused ? `恢复插入（待处理 ${pendingCount}）` : "暂停插入"}
                </Button>
            </header>
            <Card className="rounded-lg">
                <CardHeader className="flex-row flex-wrap items-center gap-2">
                    <CardTitle className="mr-auto">事件筛选</CardTitle>
                    {eventTypes.map((type) => (
                        <Button
                            key={type}
                            size="sm"
                            variant={eventType === type ? "secondary" : "outline"}
                            onClick={() => setEventType(type)}
                        >
                            {type}
                        </Button>
                    ))}
                    <label className="text-muted-foreground ml-2 flex items-center gap-2 text-xs">
                        <input
                            type="checkbox"
                            checked={watchOnly}
                            onChange={(event) => setWatchOnly(event.target.checked)}
                        />
                        仅观察组
                    </label>
                </CardHeader>
                <CardContent className="p-0">
                    {events.length ? (
                        <div className="divide-border divide-y">
                            {events.map((event, index) => {
                                const id = `${event.time}-${event.name}-${event.category}`;
                                const read = workspace.readEvents.includes(id);
                                const stock = stockQuotes.find((item) => item.name === event.name);
                                return (
                                    <article
                                        key={id}
                                        className={`grid gap-3 p-4 sm:grid-cols-[60px_minmax(0,1fr)_auto] ${read ? "opacity-55" : ""}`}
                                    >
                                        <time className="text-muted-foreground text-xs">{event.time}</time>
                                        <div>
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge className="bg-rose-500/15 text-rose-300">{event.category}</Badge>
                                                <strong>{event.name}</strong>
                                                {!read ? (
                                                    <span
                                                        className="bg-primary size-1.5 rounded-full"
                                                        aria-label="未读"
                                                    />
                                                ) : null}
                                            </div>
                                            <p className="text-muted-foreground mt-1 text-xs">
                                                {event.detail} · 事件 #{String(index + 1).padStart(3, "0")}
                                            </p>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => workspace.toggleEventRead(id)}
                                            >
                                                <IconEye data-icon="inline-start" aria-hidden="true" />
                                                {read ? "标为未读" : "标为已读"}
                                            </Button>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => workspace.locateTime(event.time)}
                                            >
                                                定位时点
                                            </Button>
                                            {stock ? (
                                                <Button size="sm" onClick={() => workspace.openStock(stock.code)}>
                                                    查看个股
                                                </Button>
                                            ) : null}
                                        </div>
                                    </article>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="py-16 text-center">
                            <p>当前筛选没有事件</p>
                            <p className="text-muted-foreground mt-2 text-xs">这是筛选零结果，不是服务错误。</p>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
