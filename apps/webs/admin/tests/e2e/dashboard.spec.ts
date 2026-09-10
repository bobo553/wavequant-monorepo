import { expect, test } from "@playwright/test";

test.describe("Admin dashboard", () => {
    test("renders the business overview and charts", async ({ page }) => {
        await page.goto("/");

        await expect(page).toHaveURL(/\/dashboard$/);
        await expect(page.getByRole("heading", { name: "经营概览" })).toBeVisible();
        await expect(page.getByText("¥ 286,400")).toBeVisible();
        await expect(page.getByRole("region", { name: "经营趋势图表" }).getByRole("img").first()).toBeVisible();
        await expect(page.getByRole("table", { name: "最近订单列表" })).toBeVisible();
    });

    test("navigates between business modules", async ({ page }) => {
        await page.goto("/dashboard");
        await page.getByRole("link", { name: "订单管理" }).click();

        await expect(page).toHaveURL(/\/dashboard\/orders$/);
        await expect(page.getByRole("heading", { name: "订单管理" })).toBeVisible();
        await expect(page.getByText("还没有可展示的订单")).toBeVisible();
    });

    test("opens the navigation drawer on a mobile viewport", async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto("/dashboard");

        await page.getByRole("button", { name: "打开侧栏" }).click();
        await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
        await page.getByRole("link", { name: "客户管理" }).click();
        await expect(page).toHaveURL(/\/dashboard\/customers$/);
    });
});
