/** Document links remount the legacy chart runtime; symbols include their exchange. */
export function stockResearchHref(code: string, date: string): string {
    const exchange = /^(60|68)\d{4}$/.test(code)
        ? "sh"
        : /^(00|30)\d{4}$/.test(code)
          ? "sz"
          : /^(43|82|83|87|88|92)\d{4}$/.test(code)
            ? "bj"
            : null;
    if (!exchange) throw new Error("不支持的 A 股代码");
    return `/research?${new URLSearchParams({ page: "workspace", symbol: `${exchange}.${code}`, asof: date, source: "akshare" })}`;
}
