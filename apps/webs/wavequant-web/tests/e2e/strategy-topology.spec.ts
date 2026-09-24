import { expect, test } from "@playwright/test";

test("research topology deep link shows tldraw decisions and source detail", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto("/research?page=topology");
    await expect(page.getByRole("heading", { name: "让每一条规则，都有去向." })).toBeVisible();
    await expect(page.locator("#page-workspace")).toBeHidden();
    await expect(page.locator("#page-title")).toHaveText("策略拓扑");
    await expect(page.getByRole("tab", { name: "① 结构与候选" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#page-topology .tl-container")).toBeVisible();
    await expect(page.locator("#page-topology .tl-shape").first()).toBeVisible();

    await page.getByRole("tab", { name: "③ A/B → C 波续攻" }).click();
    await expect(page.getByRole("tab", { name: "③ A/B → C 波续攻" })).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: /A 达到原 N 的 2T/ }).click();
    await expect(page.getByText("强 A，冻结 2T")).toBeVisible();
    await expect(page.getByText(/wave_continuation.py · wave_pullback_context/)).toBeVisible();
    await page.getByRole("tab", { name: "④ 执行与成交" }).click();
    await expect(page.getByRole("button", { name: /仓位、风险、流动性、现金足够一手/ })).toBeVisible();
    expect(consoleErrors).toEqual([]);
});

test("sidebar opens the topology on a narrow viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/research");
    await page.getByRole("button", { name: "打开侧栏" }).click();
    await page.getByRole("button", { name: "策略拓扑" }).last().click();
    await expect(page).toHaveURL(/page=topology/);
    await expect(page.getByRole("heading", { name: "让每一条规则，都有去向." })).toBeVisible();
    await expect(page.locator("#page-topology .tl-container")).toBeVisible();
});
