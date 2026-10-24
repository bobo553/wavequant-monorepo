"use client";
import type { JSX } from "react";
import type { ITopologyFlow } from "./topology-data";

/** 规则查看只呈现既有条件和出口，不创建编辑器或重新推断策略路径。 */
export function TopologyPreview({ flow, selectedId, onSelect }: {
    flow: ITopologyFlow; selectedId: string | null; onSelect: (id: string) => void;
}): JSX.Element {
    return <section aria-label="规则路径预览" className="panel min-h-[560px] min-w-0 p-4">
        <p className="text-muted-foreground mb-4 text-xs">选择判断条件查看源码依据；需要拖动缩放时可打开高级画布。</p>
        <ol className="grid gap-3 xl:grid-cols-2">
            {flow.gates.map((gate, index) => <li key={gate.id}>
                <button type="button" aria-pressed={(selectedId || flow.gates[0]?.id) === gate.id}
                    onClick={() => onSelect(gate.id)}
                    className="border-border bg-background hover:border-primary aria-pressed:border-primary w-full rounded-lg border p-4 text-left">
                    <span className="text-primary text-xs">判断 {String(index + 1).padStart(2, "0")}</span>
                    <strong className="my-2 block text-sm">{gate.question}</strong>
                    <span className="text-muted-foreground block text-xs leading-6"><b>是：</b>{gate.yes}</span>
                    <span className="text-muted-foreground block text-xs leading-6"><b>否：</b>{gate.no}</span>
                </button>
            </li>)}
        </ol>
        <p className="text-muted-foreground mt-4 text-sm">{flow.completion}</p>
    </section>;
}
