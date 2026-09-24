import { WaveQuantShell } from "@/shared/components/wavequant-shell/wavequant-shell";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ usePathname: () => "/research" }));

function renderResearchShell(): void {
    render(<WaveQuantShell>研究内容</WaveQuantShell>);
}

describe("WaveQuantShell", () => {
    beforeEach(() => {
        window.localStorage.clear();
        window.history.replaceState(null, "", "/");
    });

    it("supports persistent desktop sidebar collapsing", () => {
        renderResearchShell();

        fireEvent.click(screen.getByRole("button", { name: "收起侧栏" }));

        expect(screen.getByRole("button", { name: "展开侧栏" })).toBeInTheDocument();
        expect(window.localStorage.getItem("wavequant.sidebar.collapsed.v1")).toBe("true");
    });

    it("keeps the research page contract and mobile drawer navigation", () => {
        renderResearchShell();

        fireEvent.click(screen.getByRole("button", { name: "策略回测" }));
        expect(screen.getByRole("button", { name: "策略回测" })).toHaveAttribute("data-page", "performance");
        expect(window.location.search).toBe("?page=performance");

        fireEvent.click(screen.getByRole("button", { name: "策略拓扑" }));
        expect(window.location.search).toBe("?page=topology");
        expect(screen.getByRole("button", { name: "策略拓扑" })).toHaveAttribute("aria-current", "page");

        expect(screen.getByText("Market & Research")).toBeInTheDocument();
        expect(screen.getByText("Trading & Control")).toBeInTheDocument();
        expect(screen.getAllByText("本地研究")).toHaveLength(2);
        expect(screen.getByRole("searchbox", { name: "搜索股票" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "查看通知" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "界面设置" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "刷新当前视图" })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "打开侧栏" }));
        expect(screen.getAllByRole("button", { name: "关闭侧栏" })).toHaveLength(2);
        fireEvent.keyDown(window, { key: "Escape" });
        expect(screen.queryAllByRole("button", { name: "关闭侧栏" })).toHaveLength(0);
    });

    it("uses the header input for Ctrl+K, filtering and selecting research stocks", () => {
        const updateSearch = vi.fn();
        const submitSearch = vi.fn();
        window.addEventListener("wavequant:update-stock-search", updateSearch);
        window.addEventListener("wavequant:submit-stock-search", submitSearch);
        renderResearchShell();

        fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
        const search = screen.getByRole("searchbox", { name: "搜索股票" });
        expect(search).toHaveFocus();
        expect(screen.queryByRole("dialog", { name: "搜索股票或题材" })).not.toBeInTheDocument();

        fireEvent.change(search, { target: { value: "600519" } });
        expect(updateSearch).toHaveBeenCalledTimes(1);
        expect((updateSearch.mock.calls[0]?.[0] as CustomEvent<{ query: string }>).detail.query).toBe("600519");
        fireEvent.keyDown(search, { key: "Enter" });
        expect(submitSearch).toHaveBeenCalledTimes(1);

        window.removeEventListener("wavequant:update-stock-search", updateSearch);
        window.removeEventListener("wavequant:submit-stock-search", submitSearch);
    });
});
