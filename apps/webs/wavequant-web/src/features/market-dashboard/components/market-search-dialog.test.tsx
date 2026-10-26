import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import MarketSearchDialog from "./market-search-dialog";
import { readMarketCatalog } from "../services/market-catalog";
vi.mock("../services/market-catalog",()=>({readMarketCatalog:vi.fn()}));

describe("全市场搜索联想",()=>{
    beforeEach(()=>{vi.mocked(readMarketCatalog).mockReset();Element.prototype.scrollIntoView=vi.fn();});
    it("打开按需读取完整目录，输入名称即时联想并进入真实研究页",async()=>{
        vi.mocked(readMarketCatalog).mockResolvedValue({stocks:[{symbol:"sz.002724",name:"海洋王"}]});
        render(<MarketSearchDialog/>);
        await waitFor(()=>expect(screen.getByRole("status")).toHaveTextContent("只证券"));
        fireEvent.change(screen.getByRole("combobox"),{target:{value:"海洋"}});
        expect(screen.getByRole("option",{name:/海洋王/})).toHaveAttribute("href","/research?page=workspace&symbol=sz.002724&source=akshare");
        expect(readMarketCatalog).toHaveBeenCalledTimes(1);
        fireEvent.change(screen.getByRole("combobox"),{target:{value:"上证"}});
        expect(screen.getByRole("option",{name:/上证指数/})).toHaveAttribute("target","_blank");
    });
    it("目录失败时明确报错，指数仍可搜索并提供重试",async()=>{
        vi.mocked(readMarketCatalog).mockRejectedValue(new Error("offline"));
        render(<MarketSearchDialog/>);
        await screen.findByRole("button",{name:"重试"});
        fireEvent.change(screen.getByRole("combobox"),{target:{value:"上证指数"}});
        expect(screen.getByRole("option",{name:/上证指数/})).toBeInTheDocument();
        expect(screen.getByRole("status")).toHaveTextContent("目录暂不可用");
    });
});
