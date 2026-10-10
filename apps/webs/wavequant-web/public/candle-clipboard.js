/**
 * K 线键盘复制与原生复制共用一个范围判断。
 * 提示卡片可能挂到工作台根部或浏览器顶层，不能只凭图表容器的 :hover 判定。
 * 数据格式、剪贴板权限兼容及成功反馈仍由调用方复用现有按钮逻辑。
 */
export function bindCandleClipboard(chart, { getCandle, isEnabled, copyText, copyCandle, onCopied }) {
    const { container, tooltip } = chart;
    const doc = container.ownerDocument;
    const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]';
    const isEditable = (element) => Boolean(element?.closest?.(editable));

    const candleForEvent = (event) => {
        // 焦点停在输入框时，即使鼠标恰好悬停图表，也不能覆盖用户复制的输入内容。
        // 文字选区优先于整根 K 线，卡片内单独选择字段仍走浏览器原生复制。
        if (
            event.defaultPrevented ||
            !isEnabled() ||
            !container.isConnected ||
            container.closest("[hidden], [inert]") ||
            !tooltip.isConnected ||
            tooltip.closest("[hidden], [inert]") ||
            isEditable(doc.activeElement) ||
            event.composedPath().some(isEditable) ||
            doc.getSelection()?.toString()
        )
            return null;

        const ownsFocusOrTarget = [container, tooltip].some(
            (region) => region.contains(doc.activeElement) || region.contains(event.target),
        );
        if (!ownsFocusOrTarget && !container.matches(":hover") && !tooltip.matches(":hover")) return null;
        return getCandle();
    };

    const onKeyDown = (event) => {
        if (
            event.key.toLowerCase() !== "c" ||
            !(event.ctrlKey || event.metaKey) ||
            event.altKey ||
            event.shiftKey ||
            event.isComposing
        )
            return;
        const bar = candleForEvent(event);
        if (!bar) return;

        // 无选区的画布不保证派发 copy 事件，必须在快捷键入口直接请求写入。
        // 阻止默认复制避免重复写入；长按只消费按键，成功与失败由异步按钮逻辑反馈。
        event.preventDefault();
        if (!event.repeat) void copyCandle(bar);
    };
    const onCopy = (event) => {
        if (!event.clipboardData) return;
        const bar = candleForEvent(event);
        if (!bar) return;
        event.clipboardData.setData("text/plain", copyText(bar));
        event.preventDefault();
        onCopied(bar);
    };

    // 图表 SDK 可能截断画布事件冒泡；捕获阶段保证快捷键与右键原生复制入口一致。
    // 按钮的 execCommand 兼容复制产生的事件以 textarea 为目标，会被可编辑判断放行。
    doc.addEventListener("keydown", onKeyDown, true);
    doc.addEventListener("copy", onCopy, true);
    return () => {
        doc.removeEventListener("keydown", onKeyDown, true);
        doc.removeEventListener("copy", onCopy, true);
    };
}
