export interface IMetricItem {
    change: string;
    detail: string;
    direction: "down" | "up";
    label: string;
    value: string;
}

export interface IOrderItem {
    amount: string;
    customer: string;
    id: string;
    product: string;
    status: "处理中" | "已完成" | "待付款";
}

export const metrics: IMetricItem[] = [
    { label: "本月营收", value: "¥ 286,400", change: "+12.8%", direction: "up", detail: "较上月增加 ¥32,480" },
    { label: "新增客户", value: "1,284", change: "+8.2%", direction: "up", detail: "本月目标完成 86%" },
    { label: "订单转化率", value: "24.6%", change: "+2.4%", direction: "up", detail: "高于近 90 天均值" },
    { label: "退款率", value: "1.8%", change: "-0.6%", direction: "down", detail: "服务质量持续改善" },
];

export const revenueTrend = [
    { label: "4 月", value: 168 },
    { label: "5 月", value: 192 },
    { label: "6 月", value: 185 },
    { label: "7 月", value: 228 },
    { label: "8 月", value: 246 },
    { label: "9 月", value: 286 },
];

export const channelShare = [
    { name: "企业直销", value: 46 },
    { name: "合作伙伴", value: 31 },
    { name: "线上自助", value: 23 },
];

export const recentOrders: IOrderItem[] = [
    { id: "SO-20260910-018", customer: "星海科技", product: "企业协作套件", amount: "¥ 28,800", status: "已完成" },
    { id: "SO-20260910-017", customer: "云杉制造", product: "数据分析专业版", amount: "¥ 16,600", status: "处理中" },
    { id: "SO-20260910-016", customer: "北辰零售", product: "客户运营平台", amount: "¥ 9,980", status: "待付款" },
    { id: "SO-20260909-042", customer: "映川传媒", product: "内容管理套件", amount: "¥ 12,400", status: "已完成" },
    { id: "SO-20260909-041", customer: "远帆物流", product: "流程自动化服务", amount: "¥ 21,800", status: "处理中" },
];
