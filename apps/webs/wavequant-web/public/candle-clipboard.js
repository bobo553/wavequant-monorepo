/**
 * K 线键盘复制与原生复制共用一个范围判断。
 * 提示卡片可能挂到工作台根部或浏览器顶层，不能只凭图表容器的 :hover 判定。
 * 数据格式、剪贴板权限兼容及成功反馈仍由调用方复用现有按钮逻辑。
 */
export function bindCandleClipboard(chart, { getCandle, isEnabled, copyText, copyCandle, onCopied }) {
    const { container, tooltip } = chart;
    const doc = container.ownerDocument;
    const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]';
    const regions = [container, tooltip];

    const preservesEditorCopy = (element, pointerInChart) => {
        const field = element?.closest?.(editable);
        if (!field) return false;
        // 搜索框会 focus()+select()，选股后留下的焦点和全选仍属于旧操作。
        // 光标已经回到 K 线时以当前图表为复制意图，不清选区、不强制输入框失焦。
        // 图内编辑、编辑器自身悬停、独立输入事件及 shadow 编辑器仍归原生复制。
        if (
            field.dataset.clipboardFallback === "true" ||
            !pointerInChart ||
            field.matches(":hover") ||
            regions.some((region) => region.contains(field)) ||
            (field !== doc.activeElement && !field.contains(doc.activeElement))
        )
            return true;
        // 图外编辑器的选区保留原样；鼠标回到该编辑器后仍可正常复制其文字。
        // 兼容复制的临时 textarea 由显式标记保护，不能误把它当作残留焦点。
        return false;
    };

    const candleForEvent = (event) => {
        const pointerInChart = regions.some((region) => region.matches(":hover"));
        const selection = doc.getSelection();
        // 只按实际 DOM Range 保护选中的提示字段/正文。输入框的选区由上方
        // 编辑器意图判断负责，不能再因 Selection 回显输入文字而重复阻断。
        const selectedDocumentText = Array.from({ length: selection?.rangeCount ?? 0 }, (_, index) =>
            selection.getRangeAt(index),
        ).some((range) => !range.collapsed && range.toString());
        // 当前光标所在 K 线优先于图外旧编辑焦点，包括搜索框遗留的全选。
        // 文字选区优先于整根 K 线，卡片内单独选择字段仍走浏览器原生复制。
        if (
            event.defaultPrevented ||
            !isEnabled() ||
            !container.isConnected ||
            container.closest("[hidden], [inert]") ||
            !tooltip.isConnected ||
            tooltip.closest("[hidden], [inert]") ||
            preservesEditorCopy(doc.activeElement, pointerInChart) ||
            event.composedPath().some((element) => preservesEditorCopy(element, pointerInChart)) ||
            selectedDocumentText
        )
            return null;

        const ownsFocusOrTarget = regions.some(
            (region) => region.contains(doc.activeElement) || region.contains(event.target),
        );
        if (!ownsFocusOrTarget && !pointerInChart) return null;
        return getCandle();
    };

    const onKeyDown = (event) => {
        if (
            (event.key.toLowerCase() !== "c" && event.code !== "KeyC") ||
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
    // 按钮的 execCommand 兼容复制以带标记的 textarea 为目标，始终交给原生复制。
    doc.addEventListener("keydown", onKeyDown, true);
    doc.addEventListener("copy", onCopy, true);
    return () => {
        doc.removeEventListener("keydown", onKeyDown, true);
        doc.removeEventListener("copy", onCopy, true);
    };
}
