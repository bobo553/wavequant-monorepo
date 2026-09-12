import { describe, expect, it, vi } from "vitest";

import { AsyncViewLoadError, AsyncViewTimeoutError, LoadController } from "../src";

describe("LoadController", () => {
    it("复用并发请求并缓存成功结果", async () => {
        const loader = vi.fn(async () => "ready");
        const controller = new LoadController(loader);

        const first = controller.load();
        const second = controller.load();

        expect(first).toBe(second);
        await expect(first).resolves.toBe("ready");
        await expect(controller.load()).resolves.toBe("ready");
        expect(loader).toHaveBeenCalledTimes(1);
        expect(controller.getSnapshot()).toMatchObject({ status: "ready", attempts: 1 });
    });

    it("只重试可恢复错误，失败后必须显式 reset", async () => {
        const chunkError = new Error("Loading chunk 7 failed");
        const loader = vi.fn().mockRejectedValueOnce(chunkError).mockResolvedValueOnce("ok");
        const controller = new LoadController(loader, { retry: 1 });

        await expect(controller.load()).resolves.toBe("ok");
        expect(loader).toHaveBeenCalledTimes(2);

        const terminalLoader = vi.fn().mockRejectedValue(new Error("syntax error"));
        const terminal = new LoadController(terminalLoader, { retry: 3 });
        await expect(terminal.load()).rejects.toBeInstanceOf(AsyncViewLoadError);
        await expect(terminal.load()).rejects.toBeInstanceOf(AsyncViewLoadError);
        expect(terminalLoader).toHaveBeenCalledTimes(1);
        terminal.reset();
        await expect(terminal.load()).rejects.toBeInstanceOf(AsyncViewLoadError);
        expect(terminalLoader).toHaveBeenCalledTimes(2);
    });

    it("对超时请求重试并保持底层 import 不可取消", async () => {
        vi.useFakeTimers();
        const loader = vi.fn(() => new Promise<string>(() => undefined));
        const controller = new LoadController(loader, { timeoutMs: 20, retry: 1 });
        const result = controller.load();
        const rejection = expect(result).rejects.toMatchObject({
            cause: expect.any(AsyncViewTimeoutError),
        });

        await vi.advanceTimersByTimeAsync(41);
        await rejection;
        expect(loader).toHaveBeenCalledTimes(2);
        expect(controller.getSnapshot()).toMatchObject({ status: "failed", attempts: 2 });
        vi.useRealTimers();
    });

    it("加载中拒绝 reset", () => {
        const controller = new LoadController(() => new Promise<string>(() => undefined));
        void controller.load();
        expect(() => controller.reset()).toThrow("while it is loading");
    });

    it("timeoutMs 小于等于零时不创建超时", async () => {
        const controller = new LoadController(async () => "ready", { timeoutMs: 0 });
        await expect(controller.load()).resolves.toBe("ready");
    });
});
