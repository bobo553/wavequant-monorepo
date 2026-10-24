import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AllStocksCatalog } from "./all-stocks-catalog";
import { readMarketCatalog } from "../services/market-catalog";

vi.mock("../services/market-catalog", () => ({ readMarketCatalog: vi.fn() }));

describe("AllStocksCatalog", () => {
    beforeEach(() => vi.mocked(readMarketCatalog).mockReset());

    it("does not request a directory on mount or source selection", () => {
        render(<AllStocksCatalog />);
        fireEvent.change(screen.getByLabelText("目录数据源"), { target: { value: "tdx" } });
        expect(readMarketCatalog).not.toHaveBeenCalled();
        expect(screen.getByText("尚未请求目录，点击加载目录后获取。")).toBeInTheDocument();
    });

    it("loads only on demand and links a selected stock to real research", async () => {
        vi.mocked(readMarketCatalog).mockResolvedValue({ stocks: [
            { symbol: "sz.002724", name: "海洋王" }, { symbol: "sz.000978", name: "桂林旅游" },
        ] });
        render(<AllStocksCatalog />);
        fireEvent.click(screen.getByRole("button", { name: "加载目录" }));
        await waitFor(() => expect(screen.getByRole("link", { name: /海洋王/ })).toBeInTheDocument());
        expect(readMarketCatalog).toHaveBeenCalledTimes(1);
        expect(readMarketCatalog).toHaveBeenCalledWith("akshare", expect.any(AbortSignal), expect.any(Function));
        fireEvent.change(screen.getByLabelText("搜索全市场股票"), { target: { value: "002724" } });
        expect(screen.queryByRole("link", { name: /桂林旅游/ })).not.toBeInTheDocument();
        expect(screen.getByRole("link", { name: /海洋王/ })).toHaveAttribute(
            "href", "/research?page=workspace&symbol=sz.002724&source=akshare");
    });
});
