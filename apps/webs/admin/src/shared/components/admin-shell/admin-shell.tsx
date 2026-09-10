"use client";

import { type JSX, type ReactNode, useState } from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/shared/components/theme-toggle/theme-toggle";
import { navigationGroups, navigationLabels } from "@/shared/config/navigation";
import { Badge, Button, Input, Separator } from "@repo/design-system-web/components";
import {
    IconBell,
    IconChevronLeft,
    IconChevronRight,
    IconMenu2,
    IconSearch,
    IconSparkles,
    IconX,
} from "@tabler/icons-react";

interface IAdminShellProps {
    children: ReactNode;
}

function isNavigationItemActive(pathname: string, href: string): boolean {
    return href === "/dashboard" ? pathname === href : pathname.startsWith(href);
}

function SidebarContent({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }): JSX.Element {
    const pathname = usePathname();

    return (
        <div className="flex h-full flex-col">
            <div className="flex h-16 items-center gap-3 px-4">
                <span className="bg-primary shadow-primary/20 grid size-9 shrink-0 place-items-center rounded-xl text-white shadow-sm">
                    <IconSparkles size={19} aria-hidden="true" />
                </span>
                {collapsed ? null : (
                    <div className="min-w-0">
                        <p className="truncate text-sm font-bold">Aster Admin</p>
                        <p className="text-muted-foreground truncate text-xs">企业管理中心</p>
                    </div>
                )}
            </div>
            <Separator />
            <nav aria-label="主导航" className="flex-1 space-y-6 overflow-y-auto px-3 py-5">
                {navigationGroups.map((group) => (
                    <div key={group.label}>
                        {collapsed ? null : (
                            <p className="text-muted-foreground mb-2 px-3 text-[11px] font-semibold tracking-[0.14em] uppercase">
                                {group.label}
                            </p>
                        )}
                        <ul className="space-y-1">
                            {group.items.map((item) => {
                                const active = isNavigationItemActive(pathname, item.href);
                                const ItemIcon = item.icon;

                                return (
                                    <li key={item.href}>
                                        <Link
                                            href={item.href}
                                            aria-current={active ? "page" : undefined}
                                            title={collapsed ? item.label : undefined}
                                            onClick={onNavigate}
                                            className={`flex h-10 items-center rounded-lg text-sm font-medium transition-colors ${
                                                collapsed ? "justify-center px-2" : "gap-3 px-3"
                                            } ${
                                                active
                                                    ? "bg-primary shadow-primary/20 text-white shadow-sm"
                                                    : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                                            }`}
                                        >
                                            <ItemIcon size={19} stroke={1.8} aria-hidden="true" />
                                            {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                                        </Link>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                ))}
            </nav>
            <div className="p-3">
                <div
                    className={`border-sidebar-border bg-background/55 rounded-xl border p-3 ${collapsed ? "text-center" : ""}`}
                >
                    <span className="bg-primary/10 text-primary inline-grid size-8 place-items-center rounded-full text-xs font-bold">
                        AD
                    </span>
                    {collapsed ? null : (
                        <div className="mt-2 min-w-0">
                            <p className="truncate text-sm font-semibold">Admin User</p>
                            <p className="text-muted-foreground truncate text-xs">admin@example.com</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

/** 管理后台响应式应用壳，负责侧栏、页头和内容区。 */
export function AdminShell({ children }: IAdminShellProps): JSX.Element {
    const pathname = usePathname();
    const [collapsed, setCollapsed] = useState(false);
    const [mobileOpen, setMobileOpen] = useState(false);
    const currentLabel = navigationLabels.get(pathname) ?? "管理后台";

    return (
        <div className="bg-background min-h-screen">
            <a
                href="#main-content"
                className="bg-primary fixed top-2 left-2 z-50 -translate-y-16 rounded-md px-3 py-2 text-sm text-white focus:translate-y-0"
            >
                跳到主要内容
            </a>

            <aside
                className={`border-sidebar-border bg-sidebar fixed inset-y-0 left-0 z-30 hidden border-r transition-[width] duration-200 lg:block ${
                    collapsed ? "w-20" : "w-64"
                }`}
            >
                <SidebarContent collapsed={collapsed} />
                <Button
                    type="button"
                    variant="outline"
                    size="icon-sm"
                    aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
                    className="bg-background absolute top-20 -right-4 rounded-full"
                    onClick={() => setCollapsed((current) => !current)}
                >
                    {collapsed ? <IconChevronRight aria-hidden="true" /> : <IconChevronLeft aria-hidden="true" />}
                </Button>
            </aside>

            {mobileOpen ? (
                <div className="fixed inset-0 z-40 lg:hidden">
                    <button
                        type="button"
                        aria-label="关闭侧栏"
                        className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
                        onClick={() => setMobileOpen(false)}
                    />
                    <aside
                        id="mobile-sidebar"
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
                        <SidebarContent collapsed={false} onNavigate={() => setMobileOpen(false)} />
                    </aside>
                </div>
            ) : null}

            <div className={`transition-[padding] duration-200 ${collapsed ? "lg:pl-20" : "lg:pl-64"}`}>
                <header className="border-surface-400/70 bg-background/85 sticky top-0 z-20 flex h-16 items-center gap-3 border-b px-4 backdrop-blur-xl sm:px-6">
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label="打开侧栏"
                        aria-controls="mobile-sidebar"
                        aria-expanded={mobileOpen}
                        className="lg:hidden"
                        onClick={() => setMobileOpen(true)}
                    >
                        <IconMenu2 aria-hidden="true" />
                    </Button>
                    <div className="hidden min-w-0 items-center gap-2 text-sm sm:flex">
                        <span className="text-muted-foreground">工作台</span>
                        <span aria-hidden="true" className="text-muted-foreground">
                            /
                        </span>
                        <span className="truncate font-medium">{currentLabel}</span>
                    </div>
                    <div className="ml-auto flex items-center gap-1.5">
                        <label className="relative hidden md:block">
                            <span className="sr-only">搜索后台功能</span>
                            <IconSearch
                                size={17}
                                aria-hidden="true"
                                className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2"
                            />
                            <Input className="bg-card w-56 pl-9 xl:w-72" placeholder="搜索功能…" />
                        </label>
                        <Badge variant="outline" className="hidden xl:inline-flex">
                            演示环境
                        </Badge>
                        <Button type="button" variant="ghost" size="icon" aria-label="查看通知" className="relative">
                            <IconBell aria-hidden="true" />
                            <span className="border-background absolute top-1.5 right-1.5 size-2 rounded-full border-2 bg-red-500" />
                        </Button>
                        <ThemeToggle />
                    </div>
                </header>

                <main id="main-content" className="mx-auto w-full max-w-[1600px] p-4 sm:p-6 lg:p-8">
                    {children}
                </main>
            </div>
        </div>
    );
}
