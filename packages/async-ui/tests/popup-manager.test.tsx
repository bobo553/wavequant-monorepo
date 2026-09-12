import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
    type NoPopupProps,
    POPUP_CANCELLED,
    PopupHost,
    type PopupViewProps,
    createPopupManager,
    createPopupRegistry,
} from "../src";

function ConfirmPopup({ label, close }: PopupViewProps<{ label: string }, boolean>) {
    return <button onClick={() => close(true)}>{label}</button>;
}

function NoticePopup({ close }: PopupViewProps<NoPopupProps, void>) {
    return <button onClick={() => close()}>notice</button>;
}

function createRegistry() {
    return createPopupRegistry({
        Confirm: {
            loader: async () => ({ default: ConfirmPopup }),
            priority: 10,
            id: "confirm",
            chunkName: "confirm-popup",
            source: "popups/confirm-popup.tsx",
            preload: "intent",
        },
        Notice: async () => ({ default: NoticePopup }),
    });
}

describe("PopupManager", () => {
    it("通过 Host 注入 close 并返回类型化结果", async () => {
        const manager = createPopupManager(createRegistry(), {
            createInstanceId: () => "confirm-1",
        });
        render(<PopupHost manager={manager} />);

        const result = manager.popup(manager.popups.Confirm, { label: "confirm" });
        fireEvent.click(await screen.findByText("confirm"));

        await expect(result).resolves.toBe(true);
        expect(manager.store.getSnapshot()).toHaveLength(0);
    });

    it("popup 批量入队时先展示更高优先级，再保持串行", async () => {
        const manager = createPopupManager(createRegistry());
        const first = manager.popup("Confirm", { label: "normal" }, { instanceId: "normal", priority: 10 });
        const urgent = manager.popup("Confirm", { label: "urgent" }, { instanceId: "urgent", priority: 1 });

        await waitFor(() => expect(manager.store.getSnapshot()[0]?.context.instanceId).toBe("urgent"));
        manager.close("urgent", true);
        await expect(urgent).resolves.toBe(true);
        await waitFor(() => expect(manager.store.getSnapshot()[0]?.context.instanceId).toBe("normal"));
        manager.close("normal", false);
        await expect(first).resolves.toBe(false);
    });

    it("open 立即叠加，而取消属于无 Error 的正常控制流", async () => {
        const manager = createPopupManager(createRegistry());
        const first = manager.open("Confirm", { label: "first" }, { instanceId: "first" });
        const second = manager.open("Confirm", { label: "second" }, { instanceId: "second" });

        await waitFor(() => expect(manager.store.getSnapshot()).toHaveLength(2));
        expect(manager.cancel("first")).toBe(true);
        await expect(first).resolves.toBeUndefined();
        manager.close("second", true);
        await expect(second).resolves.toBe(true);
        expect(POPUP_CANCELLED).not.toBeInstanceOf(Error);
    });

    it("支持失败吞并模式和安全生命周期钩子", async () => {
        const onHookError = vi.fn();
        const registry = createPopupRegistry({
            Broken: {
                loader: () => Promise.reject(new Error("offline")) as Promise<{ default: typeof NoticePopup }>,
            },
        });
        const manager = createPopupManager(registry, {
            retry: 0,
            onRequest: () => {
                throw new Error("analytics unavailable");
            },
            onHookError,
        });

        await expect(manager.popup("Broken", {})).resolves.toBeUndefined();
        expect(onHookError).toHaveBeenCalledWith(expect.any(Error), "onRequest");
    });

    it("最终 Host 卸载会在微任务后取消未完成请求", async () => {
        const manager = createPopupManager(createRegistry());
        const view = render(<PopupHost manager={manager} />);
        const result = manager.open("Notice", undefined, { instanceId: "notice" });
        await waitFor(() => expect(manager.store.getSnapshot()).toHaveLength(1));

        view.unmount();
        await expect(result).resolves.toBeUndefined();
    });
});
