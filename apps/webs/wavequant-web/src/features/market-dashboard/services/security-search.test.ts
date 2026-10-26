import { searchSecurities, securitySearchItems } from "./security-search";

describe("真实证券搜索", () => {
    const rows=securitySearchItems({stocks:[{symbol:"sz.000001",name:"平安银行"},{symbol:"sz.002724",name:"海洋王"},{symbol:"sh.600519",name:"贵州茅台"}]});
    it("名称和代码联想覆盖真实目录，不使用SIM演示集合", () => {
        expect(searchSecurities(rows,"海洋")[0]?.symbol).toBe("sz.002724");
        expect(searchSecurities(rows,"６００５１９")[0]?.name).toBe("贵州茅台");
        expect(searchSecurities(rows,"sz002724")[0]?.name).toBe("海洋王");
        expect(searchSecurities(rows,"SIM001")).toHaveLength(0);
    });
    it("上证别名可联想，000001同时保留指数与平安银行的交易所身份", () => {
        expect(searchSecurities(rows,"上证").some(row=>row.symbol==="sh.000001")).toBe(true);
        expect(searchSecurities(rows,"沪指")[0]?.symbol).toBe("sh.000001");
        expect(searchSecurities(rows,"000001").map(row=>row.symbol)).toEqual(["sh.000001","sz.000001"]);
        expect(searchSecurities(rows,"sh.000001")[0]?.kind).toBe("index");
        expect(searchSecurities(rows,"sz.000001")[0]?.href).toContain("symbol=sz.000001");
    });
});
