import type { JSX } from "react";

import type { Metadata } from "next";

import { ResearchWorkbench } from "@/features/research-workbench/research-workbench";

export const metadata: Metadata = {
    title: "WaveQuant · 主控研究工作台",
    description: "主控波浪策略、通达信行情、回测、扫描与可追溯研究工作台。",
};

/** 原股票项目 Web 工作台的稳定 React 路由。 */
export default function ResearchPage(): JSX.Element {
    return <ResearchWorkbench />;
}
