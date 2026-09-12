import { fireEvent, render, screen } from "@testing-library/react";

import { MarketDashboard } from "@/features/market-dashboard/market-dashboard";
import { MarketWorkspaceProvider } from "@/features/market-dashboard/market-workspace-context";

function renderDashboard(): void {
    render(
        <MarketWorkspaceProvider>
            <MarketDashboard />
        </MarketWorkspaceProvider>,
    );
}

describe("MarketDashboard", () => {
    it("renders the overview and switches to the limit-up ladder", async () => {
        renderDashboard();

        expect(screen.getByRole("heading", { name: "市场看盘" })).toBeInTheDocument();
        expect(screen.getByText("大盘与市场广度")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: "涨停阶梯" }));

        expect(screen.getByRole("heading", { name: "涨停阶梯 · 强弱接力" })).toBeInTheDocument();
        expect(screen.getByRole("table", { name: "当前涨停股票池" })).toBeInTheDocument();
    });

    it("keeps all eight market views interactive", () => {
        renderDashboard();

        fireEvent.click(screen.getByRole("tab", { name: "板块轮动" }));
        expect(screen.getByRole("heading", { name: "板块轮动 · 同刻横向比较" })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: /异动雷达/ }));
        fireEvent.click(screen.getByRole("button", { name: "暂停插入" }));
        expect(screen.getByRole("button", { name: /恢复插入/ })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: "多股同屏" }));
        fireEvent.click(screen.getByRole("button", { name: "6 图" }));
        expect(screen.getAllByRole("img", { name: /分时百分比走势/ })).toHaveLength(6);

        fireEvent.click(screen.getByRole("tab", { name: "盘后复盘" }));
        fireEvent.change(screen.getByRole("textbox", { name: "人工研究笔记" }), { target: { value: "验证反向证据" } });
        expect(screen.getByDisplayValue("验证反向证据")).toBeInTheDocument();
    });
});
