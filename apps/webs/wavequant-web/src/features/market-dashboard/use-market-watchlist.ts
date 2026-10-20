"use client";

import { useEffect, useRef, useState } from "react";

import type { TWatchlistDocument } from "@repo/contracts";

import { stockResearchSymbol } from "./research-link";
import { requestMarketWatchlists } from "./watchlist-api";

/** 星标只在服务器确认后更新；首次加载失败仍可通过点击星标重试。 */
export function useMarketWatchlist() {
    const [document, setDocument] = useState<TWatchlistDocument | null>(null);
    const [pending, setPending] = useState(true);
    const [message, setMessage] = useState("");
    const controllerRef = useRef<AbortController | null>(null);
    const busyRef = useRef(false);

    useEffect(() => {
        const lifetime = new AbortController();
        controllerRef.current = lifetime;
        const refresh = (): void => {
            if (busyRef.current) return;
            busyRef.current = true;
            void requestMarketWatchlists(lifetime.signal)
                .then(
                    (result) => {
                        if (!lifetime.signal.aborted) setDocument(result);
                    },
                    () => {
                        if (!lifetime.signal.aborted) setMessage("自选读取失败，可点击星标重试收藏。");
                    },
                )
                .finally(() => {
                    if (!lifetime.signal.aborted) {
                        busyRef.current = false;
                        setPending(false);
                    }
                });
        };
        const handleFocus = (): void => {
            if (busyRef.current) return;
            setPending(true);
            refresh();
        };
        refresh();
        window.addEventListener("focus", handleFocus);
        return () => {
            lifetime.abort();
            busyRef.current = false;
            window.removeEventListener("focus", handleFocus);
        };
    }, []);

    const isFavorite = (code: string): boolean =>
        Boolean(
            document?.snapshot.memberships.some(
                (member) => member.groupId === "default" && member.symbol === stockResearchSymbol(code),
            ),
        );

    const toggle = async (code: string, name: string): Promise<void> => {
        const lifetime = controllerRef.current;
        if (!lifetime || lifetime.signal.aborted || busyRef.current) return;
        busyRef.current = true;
        setPending(true);
        setMessage("");
        try {
            const current = document ?? (await requestMarketWatchlists(lifetime.signal));
            const symbol = stockResearchSymbol(code);
            const added = current.snapshot.memberships.some(
                (member) => member.groupId === "default" && member.symbol === symbol,
            );
            const result = await requestMarketWatchlists(lifetime.signal, {
                action: added ? "remove" : "add",
                groupId: "default",
                stocks: [{ symbol, name }],
            });
            if (!lifetime.signal.aborted) {
                setDocument(result);
                setMessage(added ? `已取消收藏 ${name}` : `已将 ${name} 加入“我的自选”`);
            }
        } catch {
            if (!lifetime.signal.aborted) setMessage(`保存 ${name} 失败，请点击星标重试。`);
        } finally {
            if (!lifetime.signal.aborted) {
                busyRef.current = false;
                setPending(false);
            }
        }
    };
    return { isFavorite, pending, message, toggle };
}
