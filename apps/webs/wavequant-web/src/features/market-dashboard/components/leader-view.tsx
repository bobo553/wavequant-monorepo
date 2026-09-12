"use client";

import { type JSX, useState } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@repo/design-system-web/components";

import { stockQuotes } from "@/features/market-dashboard/market-data";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const roles = [
    ["height", "高度候选", "按当前仍封板的暂定/确认连板数排序"],
    ["amount", "成交中军", "按当前成交额排序，同时查看换手和容量"],
    ["gain", "日内领涨", "按相对昨收涨幅排序，不解释为买入优先级"],
    ["first", "率先封板", "按首次触板时间排序，同时核验是否开板"],
] as const;

/** 按四种公开角色展示候选、依据和反证动作。 */
export function LeaderView(): JSX.Element {
    const workspace = useMarketWorkspace();
    const [role, setRole] = useState<(typeof roles)[number][0]>("height");
    const current = roles.find((item) => item[0] === role) ?? roles[0];
    const rows = [...stockQuotes]
        .sort((left, right) =>
            role === "first"
                ? left.firstSeal.localeCompare(right.firstSeal)
                : role === "height"
                  ? right.streak - left.streak
                  : role === "gain"
                    ? Number.parseFloat(right.change) - Number.parseFloat(left.change)
                    : right.amount.localeCompare(left.amount),
        )
        .slice(0, 6);
    return (
        <div className="space-y-3">
            <header>
                <h2 className="text-lg font-bold">龙头观察 · 角色而非神秘分数</h2>
                <p className="text-muted-foreground mt-1 text-[10px]">每个角色公开排序依据，并保留反证和失效条件。</p>
            </header>
            <div className="flex flex-wrap gap-2" role="tablist" aria-label="候选角色">
                {roles.map((item) => (
                    <Button
                        key={item[0]}
                        role="tab"
                        aria-selected={role === item[0]}
                        variant={role === item[0] ? "secondary" : "outline"}
                        onClick={() => setRole(item[0])}
                    >
                        {item[1]}
                    </Button>
                ))}
            </div>
            <p className="border-primary/20 bg-primary/5 rounded-lg border p-3 text-xs">
                <strong>{current[1]}：</strong>
                <span className="text-muted-foreground">{current[2]}</span>
            </p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {rows.map((stock, index) => (
                    <Card key={stock.code} className="rounded-lg">
                        <CardHeader className="flex-row items-start justify-between">
                            <div>
                                <Badge variant="outline">
                                    #{index + 1} {current[1]}
                                </Badge>
                                <CardTitle className="mt-3">{stock.name}</CardTitle>
                                <p className="text-muted-foreground text-[10px]">
                                    {stock.code} · {stock.board}
                                </p>
                            </div>
                            <strong className="text-xl text-rose-400">
                                {role === "amount" ? stock.amount : role === "first" ? stock.firstSeal : stock.change}
                            </strong>
                        </CardHeader>
                        <CardContent>
                            <p className="text-muted-foreground text-xs leading-6">
                                反证：
                                {stock.openCount
                                    ? `已开板 ${stock.openCount} 次，需核验回封稳定性`
                                    : "尚未开板，但队列额不代表可成交或资金流入"}
                                。
                            </p>
                            <div className="mt-4 flex gap-2">
                                <Button variant="outline" onClick={() => workspace.openStock(stock.code)}>
                                    个股验证
                                </Button>
                                <Button
                                    onClick={() =>
                                        workspace.setMultiCodes(
                                            Array.from(new Set([...workspace.multiCodes, stock.code])).slice(0, 9),
                                        )
                                    }
                                >
                                    加入同屏
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>
        </div>
    );
}
