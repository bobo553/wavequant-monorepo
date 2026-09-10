import type { JSX } from "react";

import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@repo/design-system-web/components";

import type { IOrderItem } from "@/features/overview/overview-data";

interface IRecentOrdersProps {
    orders: IOrderItem[];
}

const statusVariant = {
    已完成: "success",
    处理中: "secondary",
    待付款: "warning",
} as const;

/** 展示最近订单，并在无记录时呈现明确空状态。 */
export function RecentOrders({ orders }: IRecentOrdersProps): JSX.Element {
    return (
        <Card>
            <CardHeader>
                <CardTitle>最近订单</CardTitle>
                <CardDescription>最近 24 小时产生的重点交易记录。</CardDescription>
            </CardHeader>
            <CardContent>
                {orders.length === 0 ? (
                    <div className="border-surface-400 text-muted-foreground grid min-h-56 place-items-center rounded-lg border border-dashed text-sm">
                        暂无订单记录
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[680px] text-left text-sm">
                            <caption className="sr-only">最近订单列表</caption>
                            <thead className="border-surface-400/70 text-muted-foreground border-b text-xs">
                                <tr>
                                    <th scope="col" className="pb-3 font-medium">
                                        订单号
                                    </th>
                                    <th scope="col" className="pb-3 font-medium">
                                        客户
                                    </th>
                                    <th scope="col" className="pb-3 font-medium">
                                        产品
                                    </th>
                                    <th scope="col" className="pb-3 font-medium">
                                        状态
                                    </th>
                                    <th scope="col" className="pb-3 text-right font-medium">
                                        金额
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-surface-400/60 divide-y">
                                {orders.map((order) => (
                                    <tr key={order.id} className="hover:bg-background/55 transition-colors">
                                        <td className="py-4 font-mono text-xs font-medium">{order.id}</td>
                                        <td className="py-4 font-medium">{order.customer}</td>
                                        <td className="text-muted-foreground py-4">{order.product}</td>
                                        <td className="py-4">
                                            <Badge variant={statusVariant[order.status]}>{order.status}</Badge>
                                        </td>
                                        <td className="py-4 text-right font-semibold">{order.amount}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
