import type { JSX, ReactNode } from "react";

import Link from "next/link";

import { MarketContextStatus } from "@/shared/components/market-shell/market-context-status";
import { MarketHeaderActions } from "@/shared/components/market-shell/market-header-actions";
import { Button, Separator } from "@repo/design-system-web/components";
import {
    IconChartCandle,
    IconFlask,
    IconLayersLinked,
    IconSettings,
    IconWallet,
    IconWaveSine,
} from "@tabler/icons-react";

interface IMarketShellProps {
    children: ReactNode;
}

interface INavigationItem {
    active?: boolean;
    href: string;
    icon: typeof IconChartCandle;
    label: string;
}

const navigation: INavigationItem[] = [
    { label: "市场看盘", icon: IconLayersLinked, active: true, href: "/" },
    { label: "行情与复盘", icon: IconChartCandle, href: "/research?page=workspace" },
    { label: "策略回测", icon: IconFlask, href: "/research?page=performance" },
    { label: "模拟交易", icon: IconWallet, href: "/research?page=orders" },
    { label: "信号中心", icon: IconWaveSine, href: "/research?page=orders" },
    { label: "系统与设置", icon: IconSettings, href: "/research?page=health" },
] as const;

/** WaveQuant 桌面工作台壳层，统一侧栏、顶部导航、演示声明与状态栏。 */
export function MarketShell({ children }: IMarketShellProps): JSX.Element {
    return (
        <div className="bg-background text-foreground min-h-screen lg:grid lg:grid-cols-[194px_minmax(0,1fr)]">
            <a
                href="#market-main"
                className="bg-primary text-primary-foreground fixed top-2 left-2 -translate-y-20 rounded-md px-3 py-2 text-sm font-semibold focus:translate-y-0"
            >
                跳到主要内容
            </a>
            <aside className="border-sidebar-border bg-sidebar hidden min-h-screen border-r lg:flex lg:flex-col">
                <div className="flex h-[94px] items-center gap-3 px-6">
                    <span className="border-primary/30 bg-primary/15 text-primary grid size-8 place-items-center rounded-lg border">
                        <IconWaveSine aria-hidden="true" />
                    </span>
                    <div>
                        <p className="text-base font-bold tracking-tight">WaveQuant</p>
                        <p className="text-muted-foreground text-[10px] tracking-[0.16em]">让研究有据可循</p>
                    </div>
                </div>
                <Separator />
                <nav aria-label="主导航" className="flex flex-1 flex-col px-3 py-5">
                    <p className="text-muted-foreground px-3 pb-3 text-[10px] font-semibold tracking-[0.16em] uppercase">
                        Market &amp; Research
                    </p>
                    <ul className="flex flex-col gap-1">
                        {navigation.slice(0, 3).map((item) => (
                            <NavigationItem key={item.label} {...item} />
                        ))}
                    </ul>
                    <p className="text-muted-foreground px-3 pt-8 pb-3 text-[10px] font-semibold tracking-[0.16em] uppercase">
                        Trading &amp; Control
                    </p>
                    <ul className="flex flex-col gap-1">
                        {navigation.slice(3).map((item) => (
                            <NavigationItem key={item.label} {...item} />
                        ))}
                    </ul>
                    <div className="mt-auto flex flex-col gap-3">
                        <Button asChild variant="outline" className="w-full justify-start">
                            <Link href="/wavequant-v2-classic.html">完整 v2 交互原型</Link>
                        </Button>
                        <div className="border-border bg-card/65 rounded-lg border p-3">
                            <p className="text-primary flex items-center gap-2 text-[11px] font-medium">
                                <span className="bg-primary size-1.5 rounded-full" />
                                本地演示环境
                            </p>
                            <p className="text-muted-foreground mt-2 text-[10px] leading-4">
                                无外部行情 · 无真实资金
                                <br />
                                设置与操作保存在此浏览器
                            </p>
                        </div>
                        <div className="flex items-center gap-3 px-2">
                            <span className="bg-secondary grid size-8 place-items-center rounded-lg text-xs font-semibold">
                                研
                            </span>
                            <div>
                                <p className="text-xs font-medium">个人研究空间</p>
                                <p className="text-muted-foreground text-[10px]">看盘增强原型 · v2.0</p>
                            </div>
                        </div>
                    </div>
                </nav>
            </aside>

            <div className="min-w-0">
                <header className="border-border bg-background/95 flex h-[62px] items-center border-b px-4 sm:px-8">
                    <div className="text-muted-foreground hidden items-center gap-3 text-xs sm:flex">
                        <span>研究工作台</span>
                        <span>/</span>
                        <MarketContextStatus kind="breadcrumb" />
                    </div>
                    <MarketHeaderActions />
                </header>
                <div className="border-border text-muted-foreground flex min-h-8 items-center gap-4 border-b px-4 text-[10px] sm:px-8">
                    <strong className="text-amber-400">合成样本</strong>
                    <span>· 非真实行情，不构成投资建议</span>
                    <span className="hidden sm:inline">
                        样本上涨 <b className="text-rose-400">31 只</b>
                    </span>
                    <span className="hidden sm:inline">
                        样本下跌 <b className="text-emerald-400">14 只</b>
                    </span>
                    <MarketContextStatus kind="clock" />
                    <span className="hidden xl:inline">北京时间 UTC+8</span>
                </div>
                {children}
                <footer className="border-border text-muted-foreground flex min-h-7 items-center justify-between border-t px-4 text-[10px] sm:px-8">
                    <MarketContextStatus kind="footer" />
                    <span className="hidden sm:inline">价格：元 · 数量：股 / 手 · 本地原型 · 不连接券商</span>
                </footer>
            </div>
        </div>
    );
}

function NavigationItem({ active = false, href, icon: Icon, label }: INavigationItem): JSX.Element {
    return (
        <li>
            <Button
                asChild
                variant={active ? "secondary" : "ghost"}
                className={
                    active
                        ? "border-primary/25 bg-primary/15 text-primary hover:bg-primary/20 h-11 w-full justify-start border"
                        : "text-muted-foreground h-11 w-full justify-start"
                }
            >
                <Link href={href} aria-current={active ? "page" : undefined}>
                    <Icon data-icon="inline-start" aria-hidden="true" />
                    {label}
                </Link>
            </Button>
        </li>
    );
}
