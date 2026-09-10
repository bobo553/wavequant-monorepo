import type { Icon } from "@tabler/icons-react";
import {
    IconAdjustmentsHorizontal,
    IconBuildingStore,
    IconLayoutDashboard,
    IconShoppingBag,
    IconUsers,
} from "@tabler/icons-react";

export interface INavigationItem {
    href: string;
    icon: Icon;
    label: string;
}

export interface INavigationGroup {
    items: INavigationItem[];
    label: string;
}

export const navigationGroups: INavigationGroup[] = [
    {
        label: "工作台",
        items: [{ href: "/dashboard", icon: IconLayoutDashboard, label: "经营概览" }],
    },
    {
        label: "业务管理",
        items: [
            { href: "/dashboard/orders", icon: IconShoppingBag, label: "订单管理" },
            { href: "/dashboard/customers", icon: IconUsers, label: "客户管理" },
            { href: "/dashboard/products", icon: IconBuildingStore, label: "商品管理" },
        ],
    },
    {
        label: "系统",
        items: [{ href: "/dashboard/settings", icon: IconAdjustmentsHorizontal, label: "系统设置" }],
    },
];

export const navigationLabels = new Map(
    navigationGroups.flatMap((group) => group.items.map((item) => [item.href, item.label] as const)),
);
