import type { TLimitUpStock } from "@repo/contracts";
import { fireEvent, render, screen } from "@testing-library/react";

import { LadderBoard } from "./ladder-board";

const toggle = vi.hoisted(() => vi.fn());
vi.mock("@/features/market-dashboard/use-market-watchlist", () => ({
    useMarketWatchlist: () => ({ isFavorite: () => false, pending: false, message: "", toggle }),
}));

it("keeps stock navigation separate from the card's favorite button", () => {
    const stock: TLimitUpStock = {
        code: "001216",
        name: "示例股票",
        streak: 2,
        price: 10,
        change_pct: 10,
        amount: null,
        turnover_pct: null,
        seal_amount: null,
        first_seal: null,
        last_seal: null,
        open_count: null,
        industry: "示例行业",
    };
    render(<LadderBoard stocks={[stock]} date="2026-10-20" />);
    const link = screen.getByRole("link", { name: "查看 示例股票 001216 行情与复盘" });
    expect(link).toHaveAttribute("href", "/research?page=workspace&symbol=sz.001216&asof=2026-10-20&source=akshare");
    const star = screen.getByRole("button", { name: "收藏 示例股票" });
    expect(star.closest("a")).toBeNull();
    fireEvent.click(star);
    expect(toggle).toHaveBeenCalledWith("001216", "示例股票");
});
