export const names = {
    "sh.600036": "招商银行",
    "sh.600104": "上汽集团",
    "sh.600276": "恒瑞医药",
    "sh.600519": "贵州茅台",
    "sh.600900": "长江电力",
    "sh.601318": "中国平安",
    "sz.000333": "美的集团",
    "sz.000651": "格力电器",
    "sz.000858": "五粮液",
    "sz.002415": "海康威视",
};
export const labels = {
    LONG: "入场信号",
    EXIT: "风险退出信号",
    BUY: "买入",
    SELL: "卖出",
    filled: "已成交",
    cancelled: "已取消",
    deferred: "延迟",
    rejected: "被拒绝",
    n_completed: "N 字完成",
    regime_confirmation: "盘态确认",
    strict_structure_interrupted: "严格结构中断",
    entry_rejected: "入场前置条件未通过",
    entry_preflight_rejected: "盈亏比预检未通过",
    long_signal: "生成入场信号",
    exit_signal: "风险退出观察",
    long_transition_evidence: "多头趋势链条证据",
    squeeze_resumption_observed: "轧空回压后恢复上涨",
    n_geometry_rejected: "N 字几何不成立",
    bullish_flip: "翻空为多",
    bullish_alternation: "空多交替",
    bullish_confirmation: "多头确认",
    bull_permission_revoked: "多头许可撤销",
    local_engineering: "本地工程验收",
    historical_security_data: "历史证券状态",
    exchange_calendar: "交易日历",
    daily_freshness: "行情新鲜度",
    strict_minor_paths: "次级折线路径",
    historical_universe: "历史股票池",
    independent_annotations: "独立标注",
    strategy_edge: "策略有效性",
    historical_diagnostics: "历史诊断",
    broker_and_live_permission: "券商与实盘权限",
    external_notifications: "外部通知",
    external_backup_security: "异地备份与权限",
    PASS: "通过",
    BLOCKED: "未就绪",
    UNKNOWN: "未知",
    STALE: "滞后",
    CURRENT: "已更新",
    NOT_ESTABLISHED: "尚未建立",
    EXECUTED: "已执行",
    DISABLED: "关闭",
    NOT_CONFIGURED: "未配置",
    COMPLETED: "已完成",
};
export function label(value) {
    return labels[value] || value || "—";
}
export function symbolName(symbol) {
    return `${symbol.split(".")[1] || symbol} ${names[symbol] || ""}`.trim();
}
export const pct = (v, d = 2) => (Number.isFinite(Number(v)) ? (Number(v) * 100).toFixed(d) + "%" : "N/A");
export const num = (v, d = 2) =>
    v === null || v === undefined || v === ""
        ? "—"
        : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
