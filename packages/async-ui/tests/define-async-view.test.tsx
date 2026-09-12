import { createElement } from "react";

import { render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { type AsyncViewFallbackProps, defineAsyncView } from "../src";

const resource = {
    id: "lazy-message",
    chunkName: "lazy-message",
    source: "views/lazy-message.tsx",
    preload: "rare",
} as const;

describe("defineAsyncView", () => {
    it("SSR 只输出 fallback 且不触发 loader", () => {
        const loader = vi.fn(async () => ({ default: () => createElement("p", null, "loaded") }));
        const Fallback = ({ status }: AsyncViewFallbackProps) => createElement("span", null, status);
        const View = defineAsyncView({ resource, loader, fallback: Fallback });

        expect(renderToString(createElement(View))).toContain("loading");
        expect(loader).not.toHaveBeenCalled();
    });

    it("客户端加载成功后切换为真实组件", async () => {
        const View = defineAsyncView({
            resource,
            loader: async () => ({ default: ({ name }: { name: string }) => <p>Hello {name}</p> }),
            fallback: ({ status }) => <span>{status}</span>,
        });

        render(<View name="Astar" />);
        expect(screen.getByText("loading")).toBeTruthy();
        await waitFor(() => expect(screen.getByText("Hello Astar")).toBeTruthy());
        expect(View.getStatus()).toBe("ready");
    });
});
