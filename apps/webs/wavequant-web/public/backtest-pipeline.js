const states = { pending: "等待", running: "计算中", completed: "完成", failed: "失败", not_recorded: "未提供" };
const caches = { none: "尚无缓存", buffering: "正在记录", stored: "已落盘", available: "已有对应缓存", partial: "缓存不完整", failed: "未成功落盘" };

function rowCount(step) {
    if ([1, 3, 10, 17].includes(step.id)) return `${step.rows} 个变化版本`;
    return `${step.rows} 条${step.id === 2 ? "目标事件" : step.id === 24 ? "交易记录" : "六态事件"}`;
}

function measurements(step) {
    const values = [];
    if (Number.isFinite(step.elapsed_seconds) && step.elapsed_seconds >= 0)
        values.push(`共享跨度 ${step.elapsed_seconds.toFixed(1)} 秒`);
    if (Number.isFinite(step.encoding_seconds) && step.encoding_seconds >= 0)
        values.push(`累计编码 ${step.encoding_seconds.toFixed(2)} 秒`);
    if (Number.isFinite(step.write_seconds) && step.write_seconds >= 0)
        values.push(`累计写入 ${step.write_seconds.toFixed(2)} 秒`);
    if (Number.isSafeInteger(step.encoded_bytes) && step.encoded_bytes >= 0)
        values.push(`编码内容 ${(step.encoded_bytes / 1024 / 1024).toFixed(2)} MiB`);
    if (Number.isSafeInteger(step.reused_bytes) && step.reused_bytes > 0)
        values.push(`复用编码 ${(step.reused_bytes / 1024 / 1024).toFixed(2)} MiB`);
    return values.length ? ` · ${values.join(" · ")}` : "";
}

export function pipelineRows(snapshot) {
    if (snapshot?.schema_version !== 1 || !Array.isArray(snapshot.steps) || snapshot.steps.length !== 24) return [];
    if (!snapshot.steps.every((step, index) => step?.id === index + 1 && typeof step.title === "string"
        && Object.hasOwn(states, step.status) && Object.hasOwn(caches, step.cache)
        && Number.isInteger(step.rows) && step.rows >= 0)) return [];
    return snapshot.steps.map((step) => ({
        id: step.id, status: step.status,
        text: `${step.title} · ${states[step.status]} · ${caches[step.cache]} · ${rowCount(step)}` + measurements(step),
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
