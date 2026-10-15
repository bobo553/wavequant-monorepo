const states = { pending: "等待", running: "计算中", completed: "完成", failed: "失败", not_recorded: "未提供" };
const caches = { none: "尚无缓存", buffering: "正在写入", stored: "已落盘", available: "已有对应缓存", partial: "已保存部分数据", failed: "未成功落盘" };

export function pipelineRows(snapshot) {
    if (snapshot?.schema_version !== 1 || !Array.isArray(snapshot.steps) || snapshot.steps.length !== 24) return [];
    if (!snapshot.steps.every((step, index) => step?.id === index + 1 && typeof step.title === "string"
        && Object.hasOwn(states, step.status) && Object.hasOwn(caches, step.cache)
        && Number.isInteger(step.rows) && step.rows >= 0)) return [];
    return snapshot.steps.map((step) => ({
        id: step.id, status: step.status,
        text: `${step.title} · ${states[step.status]} · ${caches[step.cache]} · ${step.rows} 条`
            + (Number.isFinite(step.elapsed_seconds) ? ` · ${step.elapsed_seconds.toFixed(1)} 秒` : ""),
        detail: step.error || step.cache_error || "",
    }));
}

export function renderBacktestPipeline(container, snapshot) {
    if (!container) return;
    const rows = pipelineRows(snapshot);
    container.hidden = !rows.length;
    if (!rows.length) return;
    const key = JSON.stringify(rows);
    if (container.dataset.snapshot === key) return;
    container.dataset.snapshot = key;
    const list = container.querySelector("ol");
    list.replaceChildren(...rows.map((row) => {
        const item = document.createElement("li");
        item.dataset.status = row.status;
        item.textContent = row.text;
        if (row.detail) item.append(document.createTextNode(` · ${row.detail}`));
        return item;
    }));
    container.querySelector("p").textContent = typeof snapshot.note === "string" ? snapshot.note : "";
}
