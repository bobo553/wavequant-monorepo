import type { JSX } from "react";

import type { TLimitUpStock } from "@repo/contracts";

import { stockResearchHref } from "@/features/market-dashboard/research-link";

const headers = [
    "日期",
    "代码",
    "名称",
    "连板数",
    "最新价",
    "涨跌幅%",
    "首封",
    "最后封板",
    "炸板次数",
    "封板资金(元)",
    "成交额(元)",
    "换手率%",
    "行业",
];

export function limitUpCsv(date: string, stocks: TLimitUpStock[]): (string | number)[][] {
    return [
        headers,
        ...stocks.map((stock) => [
            date,
            stock.code,
            stock.name,
            stock.streak,
            stock.price ?? "",
            stock.change_pct ?? "",
            stock.first_seal ?? "",
            stock.last_seal ?? "",
            stock.open_count ?? "",
            stock.seal_amount ?? "",
            stock.amount ?? "",
            stock.turnover_pct ?? "",
            stock.industry,
        ]),
    ];
}

export function LimitUpTable({ stocks, date }: { stocks: TLimitUpStock[]; date: string }): JSX.Element {
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="text-muted-foreground">
                    <tr>
                        {headers.slice(1).map((header) => (
                            <th key={header} className="px-3 py-3">
                                {header}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {limitUpCsv("", stocks)
                        .slice(1)
                        .map((row) => (
                            <tr key={row[1]} className="border-border border-t">
                                {row.slice(1).map((cell, index) => (
                                    <td key={headers[index + 1]} className="px-3 py-3">
                                        {index <= 1 ? (
                                            <a
                                                href={stockResearchHref(String(row[1]), date)}
                                                className="text-primary underline-offset-4 hover:underline focus-visible:underline"
                                                aria-label={`查看 ${row[2]} ${row[1]} 行情与复盘`}
                                            >
                                                {cell}
                                            </a>
                                        ) : cell === "" ? (
                                            "—"
                                        ) : typeof cell === "number" ? (
                                            Number(cell.toFixed(2)).toLocaleString("zh-CN")
                                        ) : (
                                            cell
                                        )}
                                    </td>
                                ))}
                            </tr>
                        ))}
                </tbody>
            </table>
        </div>
    );
}
