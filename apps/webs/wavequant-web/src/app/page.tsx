import type { JSX } from "react";

import { ResearchWorkbench } from "@/features/research-workbench/research-workbench";

/** WaveQuant 默认入口，直接由 React 渲染完整研究工作台。 */
export default function HomePage(): JSX.Element {
    return <ResearchWorkbench />;
}
