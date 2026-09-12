"use client";

import type { JSX } from "react";

import Link from "next/link";

import { Badge, Button } from "@repo/design-system-web/components";
import { IconBell, IconMenu2, IconSearch, IconSettings } from "@tabler/icons-react";

import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const legacyNavigation = [
    ["行情与复盘", "/research?page=workspace"],
    ["策略回测", "/research?page=performance"],
    ["模拟交易", "/research?page=orders"],
    ["信号中心", "/research?page=orders"],
    ["系统与设置", "/research?page=health"],
    ["完整 v2 原型", "/wavequant-v2-classic.html"],
] as const;

/** 承载全局搜索、提醒、设置和窄屏模块入口。 */
export function MarketHeaderActions(): JSX.Element {
    const { openNotifications, openSearch, openSettings } = useMarketWorkspace();
    return (
        <>
            <Button
                variant="outline"
                onClick={openSearch}
                className="mx-auto hidden w-full max-w-[365px] justify-start md:flex"
            >
                <IconSearch data-icon="inline-start" aria-hidden="true" />
                <span className="text-muted-foreground text-xs">搜索股票 / 代码 / 题材</span>
                <kbd className="border-border text-muted-foreground ml-auto rounded border px-1.5 py-0.5 text-[10px]">
                    Ctrl K
                </kbd>
            </Button>
            <div className="ml-auto flex items-center gap-1.5">
                <details className="relative lg:hidden">
                    <summary
                        className="hover:bg-accent grid size-8 cursor-pointer list-none place-items-center rounded-md"
                        aria-label="打开模块导航"
                    >
                        <IconMenu2 aria-hidden="true" />
                    </summary>
                    <nav
                        className="border-border bg-card absolute top-10 right-0 z-30 w-48 rounded-lg border p-2 shadow-xl"
                        aria-label="移动端模块导航"
                    >
                        <Button asChild variant="secondary" className="w-full justify-start">
                            <Link href="/">市场看盘</Link>
                        </Button>
                        {legacyNavigation.map(([label, href]) => (
                            <Button key={label} asChild variant="ghost" className="w-full justify-start">
                                <Link href={href}>{label}</Link>
                            </Button>
                        ))}
                    </nav>
                </details>
                <Badge variant="warning" className="hidden rounded-md sm:inline-flex">
                    演示环境
                </Badge>
                <Button variant="ghost" size="icon-sm" onClick={openSearch} className="md:hidden" aria-label="搜索股票">
                    <IconSearch aria-hidden="true" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={openNotifications} aria-label="查看通知">
                    <IconBell aria-hidden="true" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={openSettings} aria-label="界面设置">
                    <IconSettings aria-hidden="true" />
                </Button>
                <span className="bg-border mx-1 hidden h-5 w-px sm:block" />
                <span className="bg-secondary grid size-8 place-items-center rounded-lg text-xs font-semibold">研</span>
                <span className="hidden text-xs sm:inline">研究员</span>
            </div>
        </>
    );
}
