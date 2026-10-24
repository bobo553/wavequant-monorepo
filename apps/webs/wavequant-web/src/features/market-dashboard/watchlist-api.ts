import { decodeIndexedDocument } from "../../../public/indexed-document.js";
import {
    type TWatchlistDocument,
    type TWatchlistMembersMutation,
    WatchlistDocumentSchema,
    WatchlistMembersMutationSchema,
} from "@repo/contracts";

/** 行情卡片与研究页共用服务器自选文档，成员变更由服务器原子合并。 */
export async function requestMarketWatchlists(
    signal: AbortSignal,
    mutation?: TWatchlistMembersMutation,
): Promise<TWatchlistDocument> {
    const response = await fetch("/api/watchlists", {
        method: mutation ? "POST" : "GET",
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
        ...(mutation
            ? {
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(WatchlistMembersMutationSchema.parse(mutation)),
              }
            : {}),
    });
    if (!response.ok) throw new Error("自选股保存或读取失败，请重试。");
    const payload: unknown = await response.json();
    return WatchlistDocumentSchema.parse(decodeIndexedDocument(payload));
}
