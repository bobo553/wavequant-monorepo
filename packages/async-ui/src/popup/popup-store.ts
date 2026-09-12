import type { ComponentType } from "react";

import type { PopupRequestContext } from "../types";

export interface VisiblePopup {
    readonly context: PopupRequestContext<string>;
    readonly View: ComponentType<Record<string, unknown>>;
    readonly props: Readonly<Record<string, unknown>>;
}

export type PopupStoreSnapshot = readonly VisiblePopup[];

// 服务端永远不渲染弹框实例；复用同一引用可满足 useSyncExternalStore 的快照稳定性要求。
const EMPTY_SNAPSHOT: PopupStoreSnapshot = Object.freeze([]);

/**
 * PopupHost 的最小外部状态仓库。
 * 每次更新都会生成冻结的新数组，使 React 能以引用变化可靠地判断是否需要重渲染。
 */
export class PopupStore {
    private snapshot: PopupStoreSnapshot = EMPTY_SNAPSHOT;
    private readonly listeners = new Set<() => void>();

    public readonly subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    };

    public readonly getSnapshot = (): PopupStoreSnapshot => this.snapshot;

    /** SSR 固定返回空快照，弹框只在客户端宿主挂载后呈现。 */
    public readonly getServerSnapshot = (): PopupStoreSnapshot => EMPTY_SNAPSHOT;

    public add(instance: VisiblePopup): void {
        if (this.snapshot.some(({ context }) => context.instanceId === instance.context.instanceId)) {
            throw new Error(`Visible popup instance id already exists: ${instance.context.instanceId}`);
        }
        this.update([...this.snapshot, Object.freeze(instance)]);
    }

    public remove(instanceId: string): boolean {
        const next = this.snapshot.filter(({ context }) => context.instanceId !== instanceId);
        if (next.length === this.snapshot.length) {
            return false;
        }
        this.update(next);
        return true;
    }

    private update(next: readonly VisiblePopup[]): void {
        // 发布快照前冻结，避免订阅方修改共享状态并绕过通知流程。
        this.snapshot = Object.freeze(next);
        for (const listener of this.listeners) {
            listener();
        }
    }
}
