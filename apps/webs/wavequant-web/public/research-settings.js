function requireNTargetOption(enabled) {
    if (typeof enabled !== "boolean") throw new TypeError("N 一饱趋势确认开关必须为布尔值");
}

/** 有服务端上下文时以服务端为准；浏览器偏好只接住首次无上下文的选择。 */
export function researchNTargetPreference(saved, preferences) {
    return saved?.context
        ? saved.context.n_target_trend_confirmation_enabled === "true"
        : preferences?.nTargetTrendConfirmationEnabled === true;
}

/** 个股封存样本仍重新执行策略，组合封存只读取原成交账本。 */
export function researchViewRequest(scope, params, nTargetTrendConfirmationEnabled = false) {
    requireNTargetOption(nTargetTrendConfirmationEnabled);
    return scope === "stock"
        ? {
              path: "/api/stock-view",
              params: {
                  ...params,
                  n_target_trend_confirmation_enabled: String(nTargetTrendConfirmationEnabled),
              },
          }
        : { path: "/api/view", params: { ...params } };
}

/** 封存口径借已有合法服务器设置保存全局开关，不新建后台行情上下文。 */
export function watchlistSettingsToSave(snapshot, saved, enabled, nTargetTrendConfirmationEnabled) {
    requireNTargetOption(nTargetTrendConfirmationEnabled);
    const context = snapshot?.context || saved?.context;
    if (!context) return null;
    const { group: _group, cutoff: _cutoff, ...settingsContext } = context;
    return {
        enabled: snapshot ? enabled : saved.enabled,
        context: {
            ...settingsContext,
            n_target_trend_confirmation_enabled: String(nTargetTrendConfirmationEnabled),
        },
    };
}

/** 无行情队列上下文时只恢复全局开关，保留当前封存口径和方案。 */
export function watchlistSettingsRestoration(snapshot, saved, enabled, nTargetTrendConfirmationEnabled) {
    requireNTargetOption(nTargetTrendConfirmationEnabled);
    if (!saved) return null;
    if (!snapshot) {
        const selected = saved.context.n_target_trend_confirmation_enabled === "true";
        return selected === nTargetTrendConfirmationEnabled ? null : { kind: "n-target", enabled: selected };
    }
    const changed =
        saved.enabled !== enabled ||
        Object.keys(saved.context).some((key) => String(snapshot.context?.[key]) !== saved.context[key]);
    return changed ? { kind: "context", settings: saved } : null;
}
