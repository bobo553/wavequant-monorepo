import { Fragment, createElement, useEffect, useRef, useSyncExternalStore } from "react";

import type { PopupManager } from "./popup-manager";

type RegistryShape = Readonly<Record<string, unknown>>;

export interface PopupHostProps<Registry extends RegistryShape> {
    readonly manager: PopupManager<Registry>;
}

/**
 * 渲染 PopupManager 当前可见实例的唯一 React 宿主。
 * 建议放在应用根布局；manager 会阻止同一实例被两个 Host 重复渲染。
 */
export function PopupHost<Registry extends RegistryShape>({ manager }: PopupHostProps<Registry>) {
    // symbol 在组件生命周期内保持稳定，用于区分 StrictMode 重挂载与第二个真实宿主。
    const ownerRef = useRef<symbol | undefined>(undefined);
    ownerRef.current ??= Symbol("PopupHost");
    const snapshot = useSyncExternalStore(manager.subscribe, manager.getSnapshot, manager.getServerSnapshot);

    useEffect(() => manager.attachHost(ownerRef.current!), [manager]);

    return createElement(
        Fragment,
        null,
        ...snapshot.map((instance) =>
            createElement(instance.View, {
                ...instance.props,
                key: instance.context.instanceId,
                // close 最后注入，确保业务 props 无法覆盖框架控制能力。
                close: (result?: unknown) => manager.close(instance.context.instanceId, result),
            }),
        ),
    );
}
