"use client";

import { type JSX, type ReactNode, useEffect, useState } from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { MarketContextStatus } from "@/shared/components/market-shell/market-context-status";
import { MarketHeaderActions } from "@/shared/components/market-shell/market-header-actions";
import {
    selectResearchPage,
    toggleSidebarCollapsed,
    useResearchPage,
    useSidebarCollapsed,
} from "@/shared/components/wavequant-shell/wavequant-shell-state";
import { marketNavigationGroups } from "@/shared/config/wavequant-navigation";
import { Button, Separator } from "@repo/design-system-web/components";
import {
    IconChevronLeft,
    IconChevronRight,
    IconExternalLink,
    IconMenu2,
    IconWaveSine,
    IconX,
} from "@tabler/icons-react";

import { MarketWorkspaceOverlay } from "@/features/market-dashboard/components/market-workspace-overlay";
import { MarketWorkspaceProvider } from "@/features/market-dashboard/market-workspace-context";

type TWaveQuantShellVariant = "market" | "research";

interface IWaveQuantShellProps {
    children: ReactNode;
}

interface IWaveQuantSidebarProps {
    collapsed: boolean;
    onNavigate?: () => void;
    variant: TWaveQuantShellVariant;
}

function WaveQuantSidebar({ collapsed, onNavigate, variant }: IWaveQuantSidebarProps): JSX.Element {
    const activeResearchPage = useResearchPage();

    const handleResearchNavigation = (page: string): void => {
        selectResearchPage(page);
        onNavigate?.();
    };
    return (
        <div className="flex h-full flex-col">
            <div className={`flex h-16 items-center ${collapsed ? "justify-center px-2" : "gap-3 px-4"}`}>
                <span className="border-primary/30 bg-primary/15 text-primary grid size-9 shrink-0 place-items-center rounded-xl border shadow-sm">
                    <IconWaveSine size={21} aria-hidden="true" />
                </span>
                {collapsed ? null : (
                    <div className="min-w-0">
                        <p className="truncate text-sm font-bold tracking-tight">WaveQuant</p>
                        <p className="text-muted-foreground truncate text-[10px] tracking-[0.16em]">让研究有据可循</p>
                    </div>
                )}
            </div>
            <Separator />
            <nav aria-label="主导航" className="flex-1 space-y-6 overflow-y-auto px-3 py-5">
                {marketNavigationGroups.map((group) => (
                    <div key={group.label}>
                        {collapsed ? null : (
                            <p className="text-muted-foreground mb-2 px-3 text-[10px] font-semibold tracking-[0.16em] uppercase">
                                {group.label}
                            </p>
                        )}
                        <ul className="space-y-1">
                            {group.items.map((item) => {
                                const ItemIcon = item.icon;
                                const active =
                                    variant === "market"
                                        ? item.href === "/market"
                                        : item.activeForPage !== false && item.researchPage === activeResearchPage;
                                const className = `wavequant-nav-link flex h-10 w-full items-center rounded-lg border border-transparent text-sm font-medium transition-[background-color,color,border-color] ${
                                    collapsed ? "justify-center px-2" : "gap-3 px-3"
                                } ${
                                    active
                                        ? "active border-primary/25 bg-primary/15 text-primary"
                                        : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                                }`;

                                return (
                                    <li key={`${item.label}-${item.href}`}>
                                        {variant === "research" && item.researchPage ? (
                                            <button
                                                type="button"
                                                className={className}
                                                data-page={item.researchPage}
                                                title={collapsed ? item.label : undefined}
                                                aria-current={active ? "page" : undefined}
                                                onClick={() =>
                                                    handleResearchNavigation(item.researchPage ?? "workspace")
                                                }
                                            >
                                                <ItemIcon size={18} stroke={1.8} aria-hidden="true" />
                                                {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                                            </button>
                                        ) : (
                                            <Link
                                                href={item.href}
                                                className={className}
                                                title={collapsed ? item.label : undefined}
                                                aria-current={active ? "page" : undefined}
                                                onClick={onNavigate}
                                            >
                                                <ItemIcon size={18} stroke={1.8} aria-hidden="true" />
                                                {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                                            </Link>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                ))}
            </nav>
            <div className="space-y-3 p-3">
                <Button asChild variant="outline" size={collapsed ? "icon" : "default"} className="w-full">
                    <Link href="/wavequant-v2-classic.html" title={collapsed ? "完整 v2 交互原型" : undefined}>
                        <IconExternalLink aria-hidden="true" />
                        {collapsed ? <span className="sr-only">完整 v2 交互原型</span> : "完整 v2 交互原型"}
                    </Link>
                </Button>
                <div
                    className={`border-sidebar-border bg-background/55 rounded-xl border p-3 ${collapsed ? "text-center" : ""}`}
                >
                    <span className="bg-primary/10 text-primary inline-grid size-8 place-items-center rounded-full text-xs font-bold">
                        研
                    </span>
                    {collapsed ? null : (
                        <div className="mt-2 min-w-0">
                            <p className="truncate text-xs font-semibold">个人研究空间</p>
                            <p className="text-muted-foreground mt-1 truncate text-[10px]">本地只读 · 无实盘路由</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function ShellStatusStrip({ variant }: { variant: TWaveQuantShellVariant }): JSX.Element {
    return (
        <div className="border-border text-muted-foreground flex min-h-8 items-center gap-4 border-b px-4 text-[10px] sm:px-6">
            {variant === "market" ? (
                <>
                    <strong className="text-amber-400">合成样本</strong>
                    <span>· 非真实行情，不构成投资建议</span>
                    <span className="hidden sm:inline">
                        样本上涨 <b className="text-rose-400">31 只</b>
                    </span>
                    <span className="hidden sm:inline">
                        样本下跌 <b className="text-emerald-400">14 只</b>
                    </span>
                    <MarketContextStatus kind="clock" />
                </>
            ) : (
                <>
                    <strong className="text-primary">本地研究</strong>
                    <span>· 本地 / 在线历史数据，只读模式</span>
                    <span className="hidden sm:inline">通达信、AkShare 与封存回测结果</span>
                    <span className="ml-auto hidden sm:inline">无实盘路由 · 不构成投资建议</span>
                </>
            )}
        </div>
    );
}

function ShellFooter({ variant }: { variant: TWaveQuantShellVariant }): JSX.Element {
    return (
        <footer className="border-border text-muted-foreground flex min-h-8 items-center justify-between gap-4 border-t px-4 text-[10px] sm:px-6">
            {variant === "market" ? (
                <MarketContextStatus kind="footer" />
            ) : (
                <span className="text-primary font-medium">● 主控量化研究 · 只读模式</span>
            )}
            {variant === "market" ? (
                <span className="hidden sm:inline">价格：元 · 数量：股 / 手 · 本地原型 · 不连接券商</span>
            ) : (
                <span className="hidden sm:inline">
                    TradingView Lightweight Charts™ · Charts by{" "}
                    <a
                        className="hover:text-foreground underline underline-offset-2"
                        href="https://www.tradingview.com/"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        TradingView
                    </a>{" "}
                    · <a href="/vendor/NOTICE">NOTICE</a> · <a href="/vendor/LICENSE">Apache 2.0 License</a> · 只读行情
                    · 无实盘路由 · 不提供买卖操作
                </span>
            )}
        </footer>
    );
}

function ShellHeader({
    mobileOpen,
    onOpenMobile,
    variant,
}: {
    mobileOpen: boolean;
    onOpenMobile: () => void;
    variant: TWaveQuantShellVariant;
}): JSX.Element {
    return (
        <header className="border-border bg-background/80 sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b px-4 backdrop-blur-xl sm:px-6">
            <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="打开侧栏"
                aria-controls="wavequant-mobile-sidebar"
                aria-expanded={mobileOpen}
                className="lg:hidden"
                onClick={onOpenMobile}
            >
                <IconMenu2 aria-hidden="true" />
            </Button>
            <span aria-hidden="true" className="bg-border h-4 w-px lg:hidden" />
            <div className="text-muted-foreground hidden min-w-0 items-center gap-2 text-xs sm:flex">
                <span>研究工作台</span>
                <span aria-hidden="true">/</span>
                {variant === "market" ? (
                    <MarketContextStatus kind="breadcrumb" />
                ) : (
                    <strong id="page-title" className="text-foreground truncate font-medium">
                        K 线复盘
                    </strong>
                )}
            </div>
            <MarketHeaderActions researchMode={variant === "research"} />
        </header>
    );
}

function DashboardFrame({
    children,
    variant,
}: IWaveQuantShellProps & { variant: TWaveQuantShellVariant }): JSX.Element {
    const collapsed = useSidebarCollapsed();
    const [mobileOpen, setMobileOpen] = useState(false);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent): void => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "b") {
                event.preventDefault();
                toggleSidebarCollapsed();
            }
            if (event.key === "Escape") setMobileOpen(false);
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, []);

    const handleCollapsedChange = (): void => {
        toggleSidebarCollapsed();
    };

    const contentId = variant === "market" ? "market-main" : "research-main";

    return (
        <div
            className="wavequant-shell bg-background text-foreground min-h-svh"
            data-shell-structure="market"
            data-shell-variant={variant}
        >
            <a
                href={`#${contentId}`}
                className="bg-primary text-primary-foreground fixed top-2 left-2 z-50 -translate-y-16 rounded-md px-3 py-2 text-sm font-semibold focus:translate-y-0"
            >
                跳到主要内容
            </a>
            <aside
                className={`border-sidebar-border bg-sidebar fixed inset-y-0 left-0 z-30 hidden border-r transition-[width] duration-200 lg:block ${
                    collapsed ? "w-20" : "w-64"
                }`}
            >
                <WaveQuantSidebar collapsed={collapsed} variant={variant} />
                <Button
                    type="button"
                    variant="outline"
                    size="icon-sm"
                    aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
                    aria-keyshortcuts="Control+B Meta+B"
                    className="bg-background absolute top-20 -right-4 rounded-full"
                    onClick={handleCollapsedChange}
                >
                    {collapsed ? <IconChevronRight aria-hidden="true" /> : <IconChevronLeft aria-hidden="true" />}
                </Button>
            </aside>

            {mobileOpen ? (
                <div className="fixed inset-0 z-40 lg:hidden">
                    <button
                        type="button"
                        aria-label="关闭侧栏"
                        className="absolute inset-0 bg-black/55 backdrop-blur-[2px]"
                        onClick={() => setMobileOpen(false)}
                    />
                    <aside
                        id="wavequant-mobile-sidebar"
                        className="border-sidebar-border bg-sidebar relative h-full w-72 border-r shadow-2xl"
                    >
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label="关闭侧栏"
                            className="absolute top-3 right-3 z-10"
                            onClick={() => setMobileOpen(false)}
                        >
                            <IconX aria-hidden="true" />
                        </Button>
                        <WaveQuantSidebar collapsed={false} variant={variant} onNavigate={() => setMobileOpen(false)} />
                    </aside>
                </div>
            ) : null}

            <div
                className={`flex min-h-svh min-w-0 flex-col transition-[padding] duration-200 ${
                    collapsed ? "lg:pl-20" : "lg:pl-64"
                }`}
            >
                <ShellHeader mobileOpen={mobileOpen} variant={variant} onOpenMobile={() => setMobileOpen(true)} />
                <ShellStatusStrip variant={variant} />
                <div className="min-w-0 flex-1">{children}</div>
                <ShellFooter variant={variant} />
            </div>
        </div>
    );
}

export function WaveQuantShell({ children }: IWaveQuantShellProps): JSX.Element {
    const pathname = usePathname();
    const variant: TWaveQuantShellVariant = pathname.startsWith("/market") ? "market" : "research";

    return (
        <MarketWorkspaceProvider researchMode={variant === "research"}>
            <DashboardFrame variant={variant}>{children}</DashboardFrame>
            <MarketWorkspaceOverlay />
        </MarketWorkspaceProvider>
    );
}
