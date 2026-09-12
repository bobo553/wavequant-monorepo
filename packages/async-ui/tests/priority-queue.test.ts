import { describe, expect, it } from "vitest";

import { POPUP_CANCELLED, PriorityQueue } from "../src";

describe("PriorityQueue", () => {
    it("在同一微任务批次按优先级和 FIFO 顺序执行", async () => {
        const queue = new PriorityQueue();
        const order: string[] = [];
        const add = (id: string, priority: number) =>
            queue.enqueue(id, priority, async () => {
                order.push(id);
                return id;
            }).promise;

        await Promise.all([add("normal-a", 10), add("urgent", 1), add("normal-b", 10)]);
        expect(order).toEqual(["urgent", "normal-a", "normal-b"]);
    });

    it("串行等待当前任务完成", async () => {
        const queue = new PriorityQueue();
        let release!: () => void;
        const blocker = new Promise<void>((resolve) => {
            release = resolve;
        });
        const order: string[] = [];

        const first = queue.enqueue("first", 0, async () => {
            order.push("first:start");
            await blocker;
            order.push("first:end");
        }).promise;
        const second = queue.enqueue("second", 0, async () => {
            order.push("second");
        }).promise;

        await Promise.resolve();
        expect(order).toEqual(["first:start"]);
        release();
        await Promise.all([first, second]);
        expect(order).toEqual(["first:start", "first:end", "second"]);
    });

    it("取消排队任务时以稳定哨兵拒绝", async () => {
        const queue = new PriorityQueue();
        const task = queue.enqueue("cancelled", 0, async () => "unreachable");

        expect(task.cancel()).toBe(true);
        await expect(task.promise).rejects.toBe(POPUP_CANCELLED);
        expect(task.cancel()).toBe(false);
    });
});
