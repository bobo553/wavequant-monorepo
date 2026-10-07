/** 当前测幅资格由 Core 发布；封存旧版本保持各自的历史契约。 */
export function isBottomNTargetSource(event, theory) {
    return (
        event?.event === "n_completed" &&
        event.direction === "up" &&
        event.target_eligible !== false &&
        (!theory?.n_target_policy || event.target_eligible === true)
    );
}

export function bottomNTargetCaption(event) {
    if (event?.target_eligible !== true) return "";
    const bottom = event.target_bottom_date || event.shape?.[0]?.time;
    return `底部 ${bottom} · 正 N ${event.time}`;
}
