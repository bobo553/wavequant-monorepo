import type { ComponentType } from "react";

import { expectTypeOf, it } from "vitest";

import { type NoPopupProps, type PopupViewProps, createPopupManager, createPopupRegistry } from "../src";

const TypedPopup = (() => null) as ComponentType<PopupViewProps<{ playerId: string }, "saved">>;
const EmptyPopup = (() => null) as ComponentType<PopupViewProps<NoPopupProps, number>>;
const registry = createPopupRegistry({
    Player: async () => ({ default: TypedPopup }),
    Empty: async () => ({ default: EmptyPopup }),
});
const manager = createPopupManager(registry);

function compileOnly(): void {
    expectTypeOf(manager.popup("Player", { playerId: "p-1" })).toEqualTypeOf<Promise<"saved" | undefined>>();
    expectTypeOf(manager.popup("Empty")).toEqualTypeOf<Promise<number | undefined>>();

    // @ts-expect-error playerId 是 Player 弹框的必填属性。
    void manager.popup("Player", {});
    // @ts-expect-error close 是运行时保留属性，业务调用方不能传入。
    void manager.popup("Player", { playerId: "p-1", close: () => undefined });
    // @ts-expect-error registry key 必须来自已注册的字面量联合。
    void manager.popup("Unknown", {});
}

void compileOnly;

it("保留 registry 字面量 key", () => {
    expectTypeOf(manager.popups.Player).toEqualTypeOf<"Player">();
});
