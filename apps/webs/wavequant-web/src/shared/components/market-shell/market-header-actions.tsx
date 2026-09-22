"use client";

import { type JSX, useEffect, useRef } from "react";

import { Badge, Button } from "@repo/design-system-web/components";
import { IconBell, IconRefresh, IconSearch, IconSettings } from "@tabler/icons-react";

import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

/** 承载全局搜索、提醒、设置和窄屏模块入口。 */
export function MarketHeaderActions({ researchMode = false }: { researchMode?: boolean }): JSX.Element {
    const { openNotifications, openSearch, openSettings } = useMarketWorkspace();
    const researchSearchRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!researchMode) return;
        const focusSearch = (): void => {
            researchSearchRef.current?.focus();
            researchSearchRef.current?.select();
        };
        window.addEventListener("wavequant:focus-stock-search", focusSearch);
        return () => window.removeEventListener("wavequant:focus-stock-search", focusSearch);
    }, [researchMode]);

    const activateResearchSearch = (): void => {
        window.dispatchEvent(new Event("wavequant:activate-stock-search"));
    };

    const updateResearchSearch = (query: string): void => {
        window.dispatchEvent(new CustomEvent("wavequant:update-stock-search", { detail: { query } }));
    };

    return (
        <>
            {researchMode ? (
                <label className="border-input bg-background focus-within:border-ring focus-within:ring-ring/50 mx-auto flex h-10 min-w-0 flex-1 items-center gap-2 rounded-md border px-3 shadow-xs focus-within:ring-[3px] sm:max-w-[575px]">
                    <IconSearch className="text-muted-foreground size-4 shrink-0" aria-hidden="true" />
                    <span className="sr-only">搜索股票</span>
                    <input
                        id="header-stock-search"
                        ref={researchSearchRef}
                        type="search"
                        autoComplete="off"
                        aria-label="搜索股票"
                        aria-controls="stock-list"
                        aria-keyshortcuts="Control+K Meta+K"
                        placeholder="搜索股票 / 代码 / 题材"
                        className="placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent text-sm outline-none"
                        onFocus={activateResearchSearch}
                        onChange={(event) => updateResearchSearch(event.currentTarget.value)}
                        onKeyDown={(event) => {
                            if (event.nativeEvent.isComposing) return;
                            if (event.key === "Enter") {
                                event.preventDefault();
                                window.dispatchEvent(new Event("wavequant:submit-stock-search"));
                            }
                            if (event.key === "Escape") {
                                event.currentTarget.value = "";
                                updateResearchSearch("");
                            }
                        }}
                    />
                    <kbd className="border-border text-muted-foreground hidden shrink-0 rounded border px-1.5 py-0.5 text-[10px] md:inline-flex">
                        Ctrl K
                    </kbd>
                </label>
            ) : (
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
            )}
            <div className="ml-auto flex shrink-0 items-center gap-1.5">
                <Badge variant="warning" className="hidden rounded-md sm:inline-flex">
                    {researchMode ? "本地研究" : "演示环境"}
                </Badge>
                {researchMode ? null : (
                    <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={openSearch}
                        className="md:hidden"
                        aria-label="搜索股票"
                    >
                        <IconSearch aria-hidden="true" />
                    </Button>
                )}
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
