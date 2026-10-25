/** 旧ESM工具复用async-ui的并发、超时和失败缓存；只有用户打开或执行才加载。 */
export function createDeferredFeature({ loader, create, actionId, statusId, action = "start", canStart = () => true }) {
    let instance = null;
    let loading = null;
    const button = document.getElementById(actionId);
    const status = document.getElementById(statusId);
    const idleText = status.textContent;
    const controller = window.wavequantCreateFeatureLoader(loader);
    async function ensure({ announce = false } = {}) {
        if (instance) return instance;
        if (loading) return loading;
        if (controller.getStatus() === "failed") controller.reset();
        button.disabled = true;
        if (announce) status.textContent = "正在加载工具…";
        loading = controller.load().then((module) => {
            instance ||= create(module);
            if (status.textContent === "正在加载工具…") status.textContent = idleText;
            instance.contextChanged();
            if (!canStart()) button.disabled = true;
            return instance;
        }).catch(() => {
            status.textContent = "工具加载失败，请重试。";
            const retry = document.createElement("button");
            retry.type = "button";
            retry.textContent = "重新加载工具";
            retry.addEventListener("click", () => { void ensure({ announce: true }); });
            status.append(retry);
            button.disabled = !canStart();
            return null;
        }).finally(() => { loading = null; });
        return loading;
    }
    button.addEventListener("click", () => {
        // 创建后由原工具的监听器处理；首次点击在模块就绪后补执行一次。
        if (!instance && canStart()) void ensure({ announce: true }).then((feature) => { if (feature && canStart()) return feature[action](); });
    });
    return {
        ensure,
        contextChanged() {
            if (instance) instance.contextChanged();
            else button.disabled = Boolean(loading) || !canStart();
        },
        get running() { return Boolean(loading || instance?.running); },
    };
}
