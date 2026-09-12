"use client";

import type { JSX } from "react";

import { Badge, Button } from "@repo/design-system-web/components";
import { IconDownload, IconPlayerPause, IconPlayerPlay, IconSearch, IconShieldCheck } from "@tabler/icons-react";

import { marketDates, marketTimes, useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const phases = [
    { label: "竞价", index: 0 },
    { label: "早盘", index: 2 },
    { label: "盘中", index: 6 },
    { label: "盘后", index: 11 },
] as const;

/** 展示样本时间、市场范围和页面级操作的看盘工具栏。 */
export function MarketToolbar(): JSX.Element {
    const workspace = useMarketWorkspace();
    return (
        <section aria-label="看盘条件" className="border-border bg-card rounded-lg border">
            <div className="border-border flex flex-wrap items-center gap-2 border-b px-3 py-2">
                <span className="text-muted-foreground text-xs">样本日期</span>
                <select
                    aria-label="样本日期"
                    value={workspace.date}
                    onChange={(event) => workspace.setDate(event.target.value)}
                    className="border-border bg-background h-8 rounded-md border px-3 text-xs"
                >
                    {marketDates.map((date) => (
                        <option key={date}>{date}</option>
                    ))}
                </select>
                <div className="flex items-center gap-1">
                    {phases.map((phase) => (
                        <Button
                            key={phase.label}
                            size="sm"
                            variant={
                                workspace.timeIndex >= phase.index &&
                                workspace.timeIndex < (phases[phases.indexOf(phase) + 1]?.index ?? marketTimes.length)
                                    ? "secondary"
                                    : "ghost"
                            }
                            className={
                                workspace.timeIndex >= phase.index &&
                                workspace.timeIndex < (phases[phases.indexOf(phase) + 1]?.index ?? marketTimes.length)
                                    ? "text-primary"
                                    : ""
                            }
                            onClick={() => workspace.setTimeIndex(phase.index)}
                        >
                            {phase.label}
                        </Button>
                    ))}
                </div>
                <Button
                    variant="outline"
                    size="icon-sm"
                    onClick={workspace.togglePlaying}
                    aria-label={workspace.isPlaying ? "暂停盘中样本" : "播放盘中样本"}
                >
                    {workspace.isPlaying ? (
                        <IconPlayerPause aria-hidden="true" />
                    ) : (
                        <IconPlayerPlay aria-hidden="true" />
                    )}
                </Button>
                <input
                    aria-label="盘中时间"
                    type="range"
                    min="0"
                    max={marketTimes.length - 1}
                    value={workspace.timeIndex}
                    onChange={(event) => workspace.setTimeIndex(Number(event.target.value))}
                    className="market-range min-w-40 flex-1"
                />
                <strong className="text-xs tabular-nums">{workspace.time}</strong>
                <Badge variant="warning" className="rounded-md">
                    {workspace.timeIndex === 0
                        ? "虚拟竞价参考"
                        : workspace.timeIndex === marketTimes.length - 1
                          ? "收盘确认样本"
                          : "历史盘中样本"}
                </Badge>
            </div>
            <div className="flex flex-wrap items-center gap-3 px-3 py-2">
                <span className="text-muted-foreground text-xs">市场</span>
                <select
                    aria-label="市场范围"
                    value={workspace.scope}
                    onChange={(event) => workspace.setScope(event.target.value)}
                    className="border-border bg-background h-8 rounded-md border px-3 text-xs"
                >
                    <option>全样本市场</option>
                    <option>主板样本</option>
                    <option>创业板 / 科创板</option>
                    <option>北交所样本</option>
                </select>
                <label className="text-muted-foreground flex items-center gap-2 text-[11px]">
                    <input
                        type="checkbox"
                        checked={workspace.includeRisk}
                        onChange={(event) => workspace.setIncludeRisk(event.target.checked)}
                    />
                    含风险标的
                </label>
                <span className="text-muted-foreground text-[11px]">
                    {workspace.selectedSector} · 排除项与缺失可追溯
                </span>
                <span className="text-muted-foreground ml-auto text-[11px]">48 只虚构证券 · 非全市场覆盖</span>
            </div>
            <div className="absolute top-[106px] right-4 hidden items-center gap-2 xl:flex">
                <Button
                    variant="outline"
                    onClick={() =>
                        workspace.openInfo(
                            "数据状态",
                            "样本数据完整度 97.9%。48 只虚构证券中 47 只具有当前时点有效报价，1 只处于缺失演练。\n\n来源：WQ-SYNTH-02；观察时点与全部卡片共享。异常演练时保留最后可用快照并停止时钟。",
                        )
                    }
                >
                    <IconShieldCheck data-icon="inline-start" aria-hidden="true" />
                    数据状态
                </Button>
                <Button
                    variant="outline"
                    onClick={() =>
                        workspace.downloadJson(
                            `wavequant-snapshot-${workspace.date}-${workspace.time.replace(":", "")}.json`,
                        )
                    }
                >
                    <IconDownload data-icon="inline-start" aria-hidden="true" />
                    保存快照
                </Button>
                <Button onClick={workspace.openSearch}>
                    <IconSearch data-icon="inline-start" aria-hidden="true" />
                    找股票 / 题材
                </Button>
            </div>
        </section>
    );
}
