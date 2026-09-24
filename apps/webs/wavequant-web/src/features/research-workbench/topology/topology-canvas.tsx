"use client";

import { useEffect, useState } from "react";
import type { JSX } from "react";

import { type Editor, Tldraw, createShapeId, toRichText } from "tldraw";
import "tldraw/tldraw.css";

import type { ITopologyFlow, ITopologyGate } from "./topology-data";
import { TopologyShapeUtil } from "./topology-shape";

const NODE_W = 296;
const NODE_H = 150;
const RESULT_H = 102;
const STEP_X = 430;
const TOP_Y = 150;
const BRANCH_Y = 320;
const ROW_Y = 440;
const shapeUtils = [TopologyShapeUtil];
const initialBounds = { x: 38, y: 70, w: 840, h: 670 };
const compactInitialBounds = { x: 48, y: 90, w: 390, h: 500 };

function resetViewport(editor: Editor, animate = false): void {
    editor.zoomToBounds(window.innerWidth < 640 ? compactInitialBounds : initialBounds, {
        inset: window.innerWidth < 640 ? 24 : 42,
        ...(animate ? { animation: { duration: 180 } } : {}),
    });
}

function gatePosition(gate: ITopologyGate, index: number): { x: number; y: number } {
    return { x: 90 + index * STEP_X, y: TOP_Y + (gate.row ?? 0) * ROW_Y };
}

function addArrow(
    editor: Editor,
    start: { x: number; y: number },
    end: { x: number; y: number },
    label: string,
    color: "green" | "red",
): void {
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    editor.createShape({
        type: "arrow",
        x,
        y,
        props: {
            start: { x: start.x - x, y: start.y - y },
            end: { x: end.x - x, y: end.y - y },
            arrowheadEnd: "arrow",
            color,
            richText: toRichText(label),
        },
    });
}

function drawFlow(editor: Editor, flow: ITopologyFlow): void {
    const positions = new Map(flow.gates.map((gate, index) => [gate.id, gatePosition(gate, index)]));
    const completionX = 90 + flow.gates.length * STEP_X;

    for (const [index, gate] of flow.gates.entries()) {
        const position = positions.get(gate.id);
        if (!position) continue;
        const nextId = flow.gates[index + 1]?.id;
        const nextPosition = positions.get(gate.yesNext ?? nextId ?? "");
        const passPosition = nextPosition ?? { x: completionX, y: TOP_Y + (NODE_H - RESULT_H) / 2 };
        const branchTo = positions.get(gate.noNext ?? "");
        const branchPosition = branchTo ?? { x: position.x, y: position.y + BRANCH_Y };

        addArrow(
            editor,
            { x: position.x + NODE_W, y: position.y + NODE_H / 2 },
            { x: passPosition.x, y: passPosition.y + (nextPosition ? NODE_H : RESULT_H) / 2 },
            flow.mode === "exits" ? "否" : "是",
            "green",
        );
        addArrow(
            editor,
            { x: position.x + NODE_W / 2, y: position.y + NODE_H },
            { x: branchPosition.x + NODE_W / 2, y: branchPosition.y },
            flow.mode === "exits" ? "是" : "否",
            "red",
        );
    }

    for (const [index, gate] of flow.gates.entries()) {
        const position = positions.get(gate.id);
        if (!position) continue;
        editor.createShape({
            id: createShapeId(`${flow.id}-${gate.id}`),
            type: "topology-node",
            x: position.x,
            y: position.y,
            props: {
                w: NODE_W,
                h: NODE_H,
                kind: "decision",
                ordinal: String(index + 1).padStart(2, "0"),
                title: gate.question,
                source: gate.source.split(/[ ·；]/)[0] ?? "",
            },
        });
        if (gate.noNext) continue;
        editor.createShape({
            type: "topology-node",
            x: position.x,
            y: position.y + BRANCH_Y,
            props: {
                w: NODE_W,
                h: RESULT_H,
                kind: flow.mode === "exits" ? "exit" : "stop",
                ordinal: "",
                title: flow.mode === "exits" ? gate.yes : gate.no,
                source: "",
            },
        });
    }

    editor.createShape({
        type: "topology-node",
        x: completionX,
        y: TOP_Y + (NODE_H - RESULT_H) / 2,
        props: {
            w: NODE_W,
            h: RESULT_H,
            kind: "complete",
            ordinal: "",
            title: flow.completion,
            source: "",
        },
    });

    editor.user.updateUserPreferences({ colorScheme: "dark" });
    editor.updateInstanceState({ isReadonly: true, isGridMode: true });
    editor.setCurrentTool("hand");
    resetViewport(editor);
}

export function TopologyCanvas({ flow, selectedId }: { flow: ITopologyFlow; selectedId: string | null }): JSX.Element {
    const [editor, setEditor] = useState<Editor | null>(null);

    useEffect(() => {
        if (!editor || !selectedId) return;
        const gateIndex = flow.gates.findIndex((gate) => gate.id === selectedId);
        const gate = flow.gates[gateIndex];
        if (!gate) return;
        const position = gatePosition(gate, gateIndex);
        editor.zoomToBounds(
            { x: position.x - 76, y: position.y - 72, w: NODE_W + 152, h: NODE_H + 380 },
            { inset: 35, targetZoom: 1.15, animation: { duration: 180 } },
        );
        editor.select(createShapeId(`${flow.id}-${selectedId}`));
    }, [editor, flow, selectedId]);

    return (
        <div className="panel topology-canvas-panel">
            <div className="topology-panel-header">
                <div>
                    <span className="topology-panel-kicker">流程画布 / {flow.gates.length} 个判断</span>
                    <h2>{flow.label.slice(2)}</h2>
                </div>
                <div className="topology-canvas-actions" aria-label="画布缩放">
                    <button type="button" aria-label="缩小拓扑" title="缩小" onClick={() => editor?.zoomOut()}>
                        −
                    </button>
                    <button type="button" aria-label="放大拓扑" title="放大" onClick={() => editor?.zoomIn()}>
                        +
                    </button>
                    <button
                        type="button"
                        aria-label="回到拓扑起点"
                        title="回到起点"
                        onClick={() => editor && resetViewport(editor, true)}
                    >
                        回到起点
                    </button>
                </div>
            </div>
            <div className="topology-canvas-view" aria-label={`${flow.label} tldraw 拓扑画布`}>
                <Tldraw
                    hideUi
                    shapeUtils={shapeUtils}
                    onMount={(mountedEditor) => {
                        drawFlow(mountedEditor, flow);
                        setEditor(mountedEditor);
                        return () => setEditor(null);
                    }}
                />
            </div>
            <div className="topology-canvas-foot">拖动画布查看后续判断 · 滚轮缩放 · 右侧点击判断可快速定位</div>
        </div>
    );
}
