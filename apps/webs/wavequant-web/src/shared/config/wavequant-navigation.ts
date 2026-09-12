import type { Icon } from "@tabler/icons-react";
import {
    IconChartCandle,
    IconChartHistogram,
    IconFlask,
    IconLayersLinked,
    IconSettings,
    IconWallet,
} from "@tabler/icons-react";

export interface IWaveQuantNavigationItem {
    activeForPage?: boolean;
    href: string;
    icon: Icon;
    label: string;
    researchPage?: "health" | "orders" | "performance" | "workspace";
}

export interface IWaveQuantNavigationGroup {
    items: readonly IWaveQuantNavigationItem[];
    label: string;
}

/** 市场模式导航保持全景看盘原型的模块入口与命名。 */
export const marketNavigationGroups: readonly IWaveQuantNavigationGroup[] = [
    {
        label: "Market & Research",
        items: [
            { href: "/market", icon: IconLayersLinked, label: "市场看盘" },
            {
                href: "/research?page=workspace",
                icon: IconChartCandle,
                label: "行情与复盘",
                researchPage: "workspace",
            },
            {
                href: "/research?page=performance",
                icon: IconFlask,
                label: "策略回测",
                researchPage: "performance",
            },
        ],
    },
    {
        label: "Trading & Control",
        items: [
            {
                href: "/research?page=orders",
                icon: IconWallet,
                label: "模拟交易",
                researchPage: "orders",
            },
            {
                activeForPage: false,
                href: "/research?page=orders",
                icon: IconChartHistogram,
                label: "信号中心",
                researchPage: "orders",
            },
            {
                href: "/research?page=health",
                icon: IconSettings,
                label: "系统与设置",
                researchPage: "health",
            },
        ],
    },
] as const;
