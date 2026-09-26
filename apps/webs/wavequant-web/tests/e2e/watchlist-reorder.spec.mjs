import { expect, test } from "@playwright/test";

test("watchlist rows can be dragged and moved with the keyboard, then keep their order after reload", async ({
    page,
}) => {
    await page.route("**/api/catalog", (route) => route.fulfill({ json: { runs: [], variants: {} } }));
    await page.route("**/api/akshare-catalog", (route) =>
        route.fulfill({ json: { available: true, latest: "2026-09-24", with_daily: 0, stocks: [] } }),
    );
    await page.goto("/research?page=workspace");
    await expect(page.locator("#watchlist-status")).toHaveText("自选股已从浏览器本地数据加载");
    await page.evaluate(async () => {
        const request = indexedDB.open("wavequant-user-data", 1);
        const database = await new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        await new Promise((resolve, reject) => {
            const transaction = database.transaction("watchlist-memberships", "readwrite");
            const memberships = transaction.objectStore("watchlist-memberships");
            memberships.put({ groupId: "default", symbol: "sh.600001", name: "甲样本", position: 0 });
            memberships.put({ groupId: "default", symbol: "sh.600002", name: "乙样本", position: 1 });
            memberships.put({ groupId: "default", symbol: "sh.600003", name: "丙样本", position: 2 });
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
        });
        database.close();
    });
    await page.reload();

    const rows = page.locator("#watchlist-stock-list .watchlist-stock-row");
    await expect(rows).toHaveCount(3);
    const railToggle = page.locator("#watchlist-rail-toggle");
    await railToggle.scrollIntoViewIfNeeded();
    await expect(railToggle.locator(".watchlist-collapse-icon")).toBeVisible();
    await railToggle.click();
    await expect(railToggle).toHaveAttribute("aria-expanded", "false");
    await expect(railToggle.locator(".watchlist-expand-icon")).toBeVisible();
    await railToggle.click();
    await expect(railToggle).toHaveAttribute("aria-expanded", "true");
    const symbols = () => rows.evaluateAll((items) => items.map((item) => item.dataset.symbol));
    const savedSymbols = () =>
        page.evaluate(async () => {
            const request = globalThis.indexedDB.open("wavequant-user-data", 1);
            const database = await new Promise((resolve, reject) => {
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            const members = await new Promise((resolve, reject) => {
                const transaction = database.transaction("watchlist-memberships", "readonly");
                const getAll = transaction.objectStore("watchlist-memberships").getAll();
                getAll.onsuccess = () => resolve(getAll.result);
                getAll.onerror = () => reject(getAll.error);
            });
            database.close();
            return members.sort((left, right) => left.position - right.position).map((item) => item.symbol);
        });
    await expect.poll(symbols).toEqual(["sh.600001", "sh.600002", "sh.600003"]);
    const firstHandle = rows.first().locator(".watchlist-stock-drag");
    const lastRow = rows.last();
    await lastRow.scrollIntoViewIfNeeded();
    await firstHandle.scrollIntoViewIfNeeded();
    await expect(firstHandle.locator("svg")).toBeVisible();
    const start = await firstHandle.boundingBox();
    const end = await lastRow.boundingBox();
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(end.x + end.width / 2, end.y + end.height - 2, { steps: 6 });
    await page.mouse.up();
    await expect.poll(symbols).toEqual(["sh.600002", "sh.600003", "sh.600001"]);
    await expect.poll(savedSymbols).toEqual(["sh.600002", "sh.600003", "sh.600001"]);
    await page.reload();
    await expect.poll(symbols).toEqual(["sh.600002", "sh.600003", "sh.600001"]);

    const keyboardHandle = rows.last().locator(".watchlist-stock-drag");
    await keyboardHandle.focus();
    await keyboardHandle.press("ArrowUp");
    await expect.poll(symbols).toEqual(["sh.600002", "sh.600001", "sh.600003"]);
    await expect.poll(savedSymbols).toEqual(["sh.600002", "sh.600001", "sh.600003"]);
    await page.reload();
    await expect.poll(symbols).toEqual(["sh.600002", "sh.600001", "sh.600003"]);
});
