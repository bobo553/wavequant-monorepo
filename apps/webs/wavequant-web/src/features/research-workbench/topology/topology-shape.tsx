import type { JSX } from "react";

import { type Geometry2d, HTMLContainer, type RecordProps, Rectangle2d, ShapeUtil, T, type TLShape } from "tldraw";

const TOPOLOGY_SHAPE_TYPE = "topology-node";

declare module "tldraw" {
    // tldraw 固定的类型扩展接口名，不能遵循仓库的 I 前缀约定。
    // eslint-disable-next-line @typescript-eslint/naming-convention
    interface TLGlobalShapePropsMap {
        [TOPOLOGY_SHAPE_TYPE]: {
            w: number;
            h: number;
            kind: string;
            ordinal: string;
            title: string;
            source: string;
        };
    }
}

type TTopologyShape = TLShape<typeof TOPOLOGY_SHAPE_TYPE>;

/** 画布节点沿用研究工作台的面板、边框和状态色。 */
export class TopologyShapeUtil extends ShapeUtil<TTopologyShape> {
    static override type = TOPOLOGY_SHAPE_TYPE;
    static override props: RecordProps<TTopologyShape> = {
        w: T.number,
        h: T.number,
        kind: T.string,
        ordinal: T.string,
        title: T.string,
        source: T.string,
    };

    getDefaultProps(): TTopologyShape["props"] {
        return { w: 296, h: 150, kind: "decision", ordinal: "01", title: "判断条件", source: "" };
    }

    getGeometry(shape: TTopologyShape): Geometry2d {
        return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
    }

    override canEdit(): boolean {
        return false;
    }

    override canResize(): boolean {
        return false;
    }

    component(shape: TTopologyShape): JSX.Element {
        const { kind, ordinal, source, title } = shape.props;
        const isDecision = kind === "decision";
        const label = kind === "complete" ? "进入下一阶段" : kind === "exit" ? "执行动作" : "等待 / 拒绝";

        return (
            <HTMLContainer className={`topology-node topology-node--${kind}`}>
                <div className="topology-node__topline">
                    <span className="topology-node__marker" aria-hidden="true">
                        {isDecision ? "◇" : kind === "complete" ? "✓" : kind === "exit" ? "↗" : "—"}
                    </span>
                    <span>{isDecision ? `判断 ${ordinal}` : label}</span>
                </div>
                <div className="topology-node__title">{title}</div>
                {isDecision ? <div className="topology-node__source">{source}</div> : null}
            </HTMLContainer>
        );
    }

    getIndicatorPath(shape: TTopologyShape): Path2D {
        const path = new Path2D();
        path.roundRect(0, 0, shape.props.w, shape.props.h, 9);
        return path;
    }
}
