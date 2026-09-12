"use client";

import { type JSX, useState } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";
import { IconArrowRight, IconExternalLink } from "@tabler/icons-react";

import { sectorHeat, stockQuotes } from "@/features/market-dashboard/market-data";
import type { TMarketTabId } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const themeEvidence = [
    ["人形机器人", "虚构事件：新一代关节模组完成样机联调", "订单兑现与量产节奏仍未验证"],
    ["低空经济", "虚构事件：区域试验航线扩大演示范围", "适航、空域和商业化时间仍有不确定性"],
    ["电网设备", "虚构事件：智能变压设备试点公开", "项目规模和收入确认尚无真实材料"],
    ["商业航天", "虚构事件：验证任务进入联合测试阶段", "发射排期和成本假设可能发生变化"],
] as const;

/** 展示热点的催化、响应、反向证据、双时间与关联成分。 */
export function ThemeView({ onNavigate }: { onNavigate: (tab: TMarketTabId) => void }): JSX.Element {
    const workspace = useMarketWorkspace();
    const [onlyAvailable, setOnlyAvailable] = useState(false);
    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-lg font-bold">热点题材 · 证据与反证</h2>
                    <p className="text-muted-foreground mt-1 text-[10px]">
                        虚构事件不等于新闻；发布时间、可获取时间和反向证据分别保留。
                    </p>
                </div>
                <label className="text-muted-foreground flex items-center gap-2 text-xs">
                    <input
                        type="checkbox"
                        checked={onlyAvailable}
                        onChange={(event) => setOnlyAvailable(event.target.checked)}
                    />
                    仅显示当前时点可获取材料
                </label>
            </header>
            <div className="grid gap-3 lg:grid-cols-2">
                {themeEvidence
                    .filter((_, index) => !onlyAvailable || index < 3)
                    .map(([name, catalyst, counter], index) => (
                        <Card key={name} className="rounded-lg">
                            <CardHeader className="flex-row items-start justify-between">
                                <div>
                                    <CardTitle>{name}</CardTitle>
                                    <p className="text-muted-foreground mt-1 text-[10px]">
                                        发布 09:{18 + index * 7} · 可获取 09:{22 + index * 8} · 虚构来源 WQ-DEMO
                                    </p>
                                </div>
                                <Badge className="bg-rose-500/15 text-rose-300">{sectorHeat[index]?.change}</Badge>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="border-primary/20 bg-primary/5 rounded-lg border p-3">
                                    <strong className="text-xs">事件证据</strong>
                                    <p className="text-muted-foreground mt-2 text-xs leading-6">{catalyst}</p>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="mt-2"
                                        onClick={() =>
                                            workspace.openInfo(
                                                `${name} · 虚构事件材料`,
                                                `${catalyst}\n\n发布时间与可获取时间分开保存；本材料只用于演示事件核验，不对应真实公司或公告。`,
                                            )
                                        }
                                    >
                                        核对材料时间
                                        <IconExternalLink data-icon="inline-end" aria-hidden="true" />
                                    </Button>
                                </div>
                                <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3">
                                    <strong className="text-xs text-amber-300">反向证据 / 失效条件</strong>
                                    <p className="text-muted-foreground mt-2 text-xs leading-6">{counter}</p>
                                </div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <Button
                                        variant="outline"
                                        onClick={() => workspace.openStock(stockQuotes[index]?.code ?? "SIM001")}
                                    >
                                        核查领涨候选
                                    </Button>
                                    <Button
                                        onClick={() => {
                                            workspace.setSelectedSector(name);
                                            onNavigate("overview");
                                        }}
                                    >
                                        联动此题材
                                        <IconArrowRight data-icon="inline-end" aria-hidden="true" />
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
            </div>
        </div>
    );
}
