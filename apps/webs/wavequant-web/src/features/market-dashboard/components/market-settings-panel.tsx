import type { JSX } from "react";

import type { TMarketColorTheme } from "@/features/market-dashboard/market-types";
import { useMarketWorkspace } from "@/features/market-dashboard/market-workspace-context";

const colorThemes: Array<{ label: string; value: TMarketColorTheme }> = [
    { label: "WaveQuant 青", value: "wavequant-teal" },
    { label: "证券蓝（推荐）", value: "market-blue" },
];

export function MarketSettingsPanel(): JSX.Element {
    const workspace = useMarketWorkspace();
    return (
        <div className="space-y-5 p-5">
            <Setting label="明暗模式" description="切换深色或浅色工作区">
                <select
                    aria-label="主题"
                    value={workspace.theme}
                    onChange={(event) => workspace.setTheme(event.target.value === "light" ? "light" : "dark")}
                    className="border-border bg-background h-9 rounded-md border px-3"
                >
                    <option value="dark">深色</option>
                    <option value="light">浅色</option>
                </select>
            </Setting>
            <Setting label="UI 主题色" description="蓝色用于操作和选中；红绿只表达行情方向">
                <select
                    aria-label="UI 主题色"
                    value={workspace.colorTheme}
                    onChange={(event) =>
                        workspace.setColorTheme(event.target.value === "market-blue" ? "market-blue" : "wavequant-teal")
                    }
                    className="border-border bg-background h-9 rounded-md border px-3"
                >
                    {colorThemes.map((theme) => (
                        <option key={theme.value} value={theme.value}>
                            {theme.label}
                        </option>
                    ))}
                </select>
            </Setting>
            <Setting label="涨跌配色" description="替代配色仍保留正负号与文字">
                <select
                    aria-label="涨跌配色"
                    value={workspace.palette}
                    onChange={(event) =>
                        workspace.setPalette(event.target.value === "accessible" ? "accessible" : "classic")
                    }
                    className="border-border bg-background h-9 rounded-md border px-3"
                >
                    <option value="classic">红涨绿跌</option>
                    <option value="accessible">蓝橙替代</option>
                </select>
            </Setting>
            <Setting label="信息密度" description="控制卡片与表格的垂直间距">
                <select
                    aria-label="信息密度"
                    value={workspace.density}
                    onChange={(event) =>
                        workspace.setDensity(event.target.value === "compact" ? "compact" : "comfortable")
                    }
                    className="border-border bg-background h-9 rounded-md border px-3"
                >
                    <option value="comfortable">舒适</option>
                    <option value="compact">紧凑</option>
                </select>
            </Setting>
            <p className="rounded-lg border border-amber-400/20 bg-amber-400/8 p-3 text-xs text-amber-300">
                主题和工作区设置保存在浏览器的 wavequant.market.v2 空间，不上传到服务器。
            </p>
        </div>
    );
}

function Setting({
    children,
    description,
    label,
}: {
    children: JSX.Element;
    description: string;
    label: string;
}): JSX.Element {
    return (
        <label className="flex items-center justify-between gap-5">
            <span>
                <strong className="block text-sm">{label}</strong>
                <small className="text-muted-foreground mt-1 block">{description}</small>
            </span>
            {children}
        </label>
    );
}
