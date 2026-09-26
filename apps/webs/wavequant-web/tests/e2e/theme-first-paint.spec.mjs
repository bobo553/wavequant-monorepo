import { expect, test } from "@playwright/test";

test("saved light theme is applied before the first frame", async ({ page }) => {
    await page.addInitScript(() => {
        globalThis.localStorage.setItem(
            "wavequant.market.v2",
            JSON.stringify({ version: 2, state: { theme: "light", colorTheme: "market-blue" } }),
        );
        globalThis.__firstThemeFrame = new Promise((resolve) =>
            globalThis.requestAnimationFrame(() =>
                resolve({
                    className: globalThis.document.documentElement.className,
                    background: globalThis.getComputedStyle(globalThis.document.documentElement).backgroundColor,
                }),
            ),
        );
    });
    await page.route("**/theme-init.js", async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 1_000));
        await route.continue();
    });

    await page.goto("/research?page=workspace", { waitUntil: "domcontentloaded" });
    const firstFrame = await page.evaluate(() => globalThis.__firstThemeFrame);

    expect(firstFrame).toEqual({ className: "light", background: "rgb(243, 246, 250)" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "market-blue");
    await expect(page.locator("#wavequant-theme-init")).toHaveCount(1);
    expect(await page.locator("#wavequant-theme-init").getAttribute("src")).toBeNull();
});
