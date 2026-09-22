"use client";

import { useEffect, useState } from "react";

import type { TLimitUpLadder } from "@repo/contracts";

import { loadLimitUpLadder } from "@/features/market-dashboard/limit-up-api";

interface ILadderResult {
    key: string;
    data?: TLimitUpLadder;
    error?: string;
}

export function useLimitUpLadder(date: string) {
    const [revision, setRevision] = useState(0);
    const [result, setResult] = useState<ILadderResult | null>(null);
    const key = `${date}:${revision}`;
    useEffect(() => {
        const controller = new AbortController();
        void loadLimitUpLadder(date, revision > 0, controller.signal).then(
            (data) => {
                if (!controller.signal.aborted) setResult({ key, data });
            },
            () => {
                if (!controller.signal.aborted) setResult({ key, error: "涨停池读取失败，请检查服务或稍后重试。" });
            },
        );
        return () => controller.abort();
    }, [date, key, revision]);
    const current = result?.key === key ? result : null;
    return {
        data: current?.data,
        error: current?.error,
        loading: current === null,
        refresh: () => setRevision((value) => value + 1),
    };
}
