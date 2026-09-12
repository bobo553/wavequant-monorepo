"use client";

import type { JSX } from "react";

import { Badge, Button } from "@repo/design-system-web/components";
import { IconBell, IconRefresh, IconSearch, IconSettings } from "@tabler/icons-react";

import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** 承载全局搜索、提醒、设置和窄屏模块入口。 */
export function MarketHeaderActions({ researchMode = false }: { researchMode?: boolean }): JSX.Element {
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
                <Badge variant="warning" className="hidden rounded-md sm:inline-flex">
                    {researchMode ? "本地研究" : "演示环境"}
                </Badge>
                <Button variant="ghost" size="icon-sm" onClick={openSearch} className="md:hidden" aria-label="搜索股票">
                    <IconSearch aria-hidden="true" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={openNotifications} aria-label="查看通知">
                    <IconBell aria-hidden="true" />
                </Button>
                <Button
                    id="reload"
                    variant="ghost"
                    size="icon-sm"
                    title="刷新当前视图"
                    aria-label="刷新当前视图"
                    aria-hidden={!researchMode}
                    tabIndex={researchMode ? undefined : -1}
                    className={researchMode ? undefined : "hidden"}
                >
                    <IconRefresh aria-hidden="true" />
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
