import { expect, test } from "@playwright/test";

const widgetScript = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";

test("TradingView drawing view is lazy, keeps the local chart, and synchronizes the chosen stock explicitly", async ({
    page,
}) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    let widgetLoads = 0;
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route(widgetScript, (route) => {
        widgetLoads += 1;
        return route.fulfill({
            contentType: "text/javascript",
            body: `(() => { const script = document.currentScript;
                const config = JSON.parse(script.textContent);
                const frame = document.createElement("iframe");
                frame.title = "TradingView test chart";
                frame.dataset.symbol = config.symbol;
                frame.dataset.drawingToolbar = String(!config.hide_side_toolbar);
                frame.dataset.theme = config.theme;
                frame.style.width = "100%";
                frame.style.height = "100%";
                script.parentElement.querySelector(".tradingview-widget-container__widget").append(frame); })();`,
        });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await expect(page.locator("#price-chart")).toBeVisible();
    expect(widgetLoads).toBe(0);
    await page.locator("#header-stock-search").fill("星网锐捷");
    await page.locator('[data-symbol="sz.002396"]').click();
    await expect(page.locator("#symbol-select")).toHaveValue("sz.002396");
    await expect(page.locator("#result-scope")).toHaveValue("akshare");
    await page.evaluate(() => {
        document.documentElement.classList.remove("dark");
        document.documentElement.classList.add("light");
    });
    await page.locator("#chart-tradingview-tab").click();
    await expect(page.locator("#tradingview-widget iframe")).toHaveAttribute("data-symbol", "SZSE:002396");
    await expect(page.locator("#tradingview-widget iframe")).toHaveAttribute("data-drawing-toolbar", "true");
    await expect(page.locator("#tradingview-widget iframe")).toHaveAttribute("data-theme", "light");
    await expect(page.locator("#price-chart")).toBeHidden();
    await expect(page.locator("#tradingview-open")).toHaveAttribute("href", /SZSE%3A002396/);
    expect(widgetLoads).toBe(1);

    await page.locator("#chart-local-tab").click();
    await expect(page.locator("#price-chart")).toBeVisible();
    await expect(page.locator("#tradingview-panel")).toBeHidden();
    await page.locator("#chart-local-tab").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator("#chart-tradingview-tab")).toHaveAttribute("aria-selected", "true");
    expect(widgetLoads).toBe(1);

    if (await page.locator("#stock-picker-panel").isHidden()) await page.locator("#stock-picker-toggle").click();
    await page.locator("#header-stock-search").fill("贵州茅台");
    await page.locator('[data-symbol="sh.600519"]').click();
    await expect(page.locator("#symbol-select")).toHaveValue("sh.600519");
    await expect(page.locator("#tradingview-sync")).toBeVisible();
    await expect(page.locator("#tradingview-widget iframe")).toHaveAttribute("data-symbol", "SZSE:002396");
    await page.locator("#tradingview-sync").click();
    await expect(page.locator("#tradingview-widget iframe")).toHaveAttribute("data-symbol", "SSE:600519");
    expect(widgetLoads).toBe(2);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("#tradingview-panel")).toBeVisible();
    expect(
        await page
            .locator("#tradingview-panel")
            .evaluate((element) => element.getBoundingClientRect().right <= innerWidth),
    ).toBe(true);
    expect(pageErrors).toEqual([]);
});

test("TradingView network failure leaves the local research chart and a retry path available", async ({ page }) => {
    await page.route(widgetScript, (route) => route.abort("failed"));
    await page.goto("/research?page=workspace");
    await expect(page.locator("#loading")).toBeHidden({ timeout: 60_000 });
    await page.locator("#chart-tradingview-tab").click();
    await expect(page.locator("#tradingview-status")).toContainText("未能加载");
    await expect(page.locator("#tradingview-retry")).toBeVisible();
    await expect(page.locator("#tradingview-open")).toBeVisible();
    await page.locator("#chart-local-tab").click();
    await expect(page.locator("#price-chart")).toBeVisible();
});
