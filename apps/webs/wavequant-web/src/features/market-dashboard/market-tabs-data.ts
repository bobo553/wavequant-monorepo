import type { IMarketTab } from "./market-types";

export const marketTabs: IMarketTab[] = [
    { id: "overview", label: "市场总览" },
    { id: "catalog", label: "全市场股票" },
    { id: "sectors", label: "板块轮动" },
    { id: "themes", label: "热点题材" },
    { id: "ladder", label: "涨停阶梯" },
    { id: "leaders", label: "龙头观察" },
    { id: "radar", label: "异动雷达", count: 67 },
    { id: "multi", label: "多股同屏" },
    { id: "review", label: "盘后复盘" },
];
