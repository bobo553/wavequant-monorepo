"use client";
import { useEffect } from "react";
import { useResearchPage } from "@/shared/components/wavequant-shell/wavequant-shell-state";

/** DOM提交后通知旧研究运行时；导入完成不等于节点已经存在。 */
export function useResearchSection(page: string): boolean {
    const active = useResearchPage() === page;
    useEffect(() => {
        if (active) window.dispatchEvent(new CustomEvent("wavequant:research-section-ready", { detail: { page } }));
    }, [active, page]);
    return active;
}
