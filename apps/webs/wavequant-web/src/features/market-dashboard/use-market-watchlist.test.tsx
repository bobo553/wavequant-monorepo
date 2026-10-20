import { WatchlistDocumentSchema } from "@repo/contracts";
import { act, renderHook, waitFor } from "@testing-library/react";

import { useMarketWatchlist } from "./use-market-watchlist";

const empty = {
    revision: 0,
    settings: null,
    snapshot: {
        schemaVersion: 1,
        groups: [{ id: "default", name: "我的自选", position: 0, protected: true }],
        memberships: [],
    },
};
const added = {
    ...empty,
    revision: 1,
    snapshot: {
        ...empty.snapshot,
        memberships: [{ groupId: "default", symbol: "sz.001216", name: "示例", position: 0 }],
    },
};
const response = (value: unknown): Response =>
    new Response(JSON.stringify(value), {
        headers: { "Content-Type": "application/json" },
    });

afterEach(() => vi.unstubAllGlobals());

describe("market watchlist", () => {
    it("persists the exact stock and can remove it without replacing the document", async () => {
        const request = vi
            .fn<typeof fetch>()
            .mockResolvedValueOnce(response(empty))
            .mockResolvedValueOnce(response(added))
            .mockResolvedValueOnce(response(empty));
        vi.stubGlobal("fetch", request);
        const { result } = renderHook(() => useMarketWatchlist());
        await waitFor(() => expect(result.current.pending).toBe(false));
        await act(() => result.current.toggle("001216", "示例"));
        expect(result.current.isFavorite("001216")).toBe(true);
        expect(JSON.parse(String(request.mock.calls[1]?.[1]?.body))).toEqual({
            action: "add",
            groupId: "default",
            stocks: [{ symbol: "sz.001216", name: "示例" }],
        });
        await act(() => result.current.toggle("001216", "示例"));
        expect(result.current.isFavorite("001216")).toBe(false);
        expect(String(request.mock.calls[2]?.[1]?.body)).toContain('"action":"remove"');
    });

    it("does not claim success on failure and allows retry", async () => {
        const request = vi
            .fn<typeof fetch>()
            .mockResolvedValueOnce(response(empty))
            .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
            .mockResolvedValueOnce(response(added));
        vi.stubGlobal("fetch", request);
        const { result } = renderHook(() => useMarketWatchlist());
        await waitFor(() => expect(result.current.pending).toBe(false));
        await act(() => result.current.toggle("001216", "示例"));
        expect(result.current.isFavorite("001216")).toBe(false);
        expect(result.current.message).toContain("失败");
        await act(() => result.current.toggle("001216", "示例"));
        expect(result.current.isFavorite("001216")).toBe(true);
    });

    it("accepts server documents larger than the old total quota", () => {
        const memberships = Array.from({ length: 20_001 }, (_, position) => ({
            groupId: "default",
            symbol: `sz.${String(position).padStart(6, "0")}`,
            name: "股票",
            position,
        }));
        expect(
            WatchlistDocumentSchema.parse({ ...empty, snapshot: { ...empty.snapshot, memberships } }).snapshot
                .memberships,
        ).toHaveLength(20_001);
    });
});
