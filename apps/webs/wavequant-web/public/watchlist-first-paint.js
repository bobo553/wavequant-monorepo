/* 自选首屏交互：不依赖React、图表、SDK或研究配置；数据修改仍由正式运行时处理。 */
(() => {
    if (!['/', '/research', '/research/'].includes(location.pathname)) return;
    const KEY = 'wavequant.watchlists.preview.v2';
    const OLD_KEY = 'wavequant.watchlists.preview.v1';
    const LIMIT = 256 * 1024;
    let released = false;
    let mounted = false;
    const shadows = new Map();
    function preview(value) {
        const snapshot = value?.snapshot;
        if (!Number.isSafeInteger(value?.revision) || value.revision < 0 || snapshot?.schemaVersion !== 1 ||
            !Array.isArray(snapshot.groups) || !snapshot.groups.length || snapshot.groups.length > 20 ||
            !Array.isArray(snapshot.memberships) || snapshot.memberships.length > 10000) throw Error('invalid preview');
        const ids = new Set();
        for (const group of snapshot.groups) {
            if (typeof group.id !== 'string' || !group.id.length || group.id.length > 64 ||
                typeof group.name !== 'string' || group.name.length > 24 || ids.has(group.id)) throw Error('invalid group');
            ids.add(group.id);
        }
        for (const member of snapshot.memberships) {
            if (!ids.has(member.groupId) || !/^(sh|sz|bj)\.\d{6}$/.test(member.symbol) ||
                typeof member.name !== 'string' || member.name.length > 80 ||
                !Number.isSafeInteger(member.position) || member.position < 0) throw Error('invalid member');
        }
        return { revision: value.revision, snapshot, settings: null };
    }
    let initial = null;
    try {
        const raw = localStorage.getItem(KEY) || localStorage.getItem(OLD_KEY);
        if (raw && raw.length <= LIMIT / 2) initial = preview(JSON.parse(raw));
    } catch { /* 缓存损坏或不可用时继续服务器缓存。 */ }
    const boot = window.wavequantWatchlistBoot = {
        preview: initial,
        source: initial ? 'local' : null,
        fresh: null,
        error: false,
        runtimeReady: false,
        pendingSymbol: null,
        groupId: null,
        query: "",
        select(symbol) {
            if (!this.preview?.snapshot.memberships.some((member) => member.symbol === symbol)) return;
            this.pendingSymbol = symbol;
            performance.mark?.('wavequant:watchlist-selection');
            for (const button of document.getElementById('watchlist-first-paint')?.shadowRoot?.querySelectorAll('button[data-symbol]') || []) {
                button.setAttribute('aria-pressed', String(button.dataset.symbol === symbol));
                button.closest('.watchlist-stock-row').dataset.selected = String(button.dataset.symbol === symbol);
            }
            paintHeader(symbol);
            const member = this.preview.snapshot.memberships.find((item) => item.symbol === symbol);
            text('watchlist-status', `已选中 ${member.name || symbol}，正在准备图表…`);
        },
        release() {
            if (!this.runtimeReady) return;
            released = true;
            observer.disconnect();
            for (const [host, shadow] of shadows) {
                // 标题在分析运行时接管后才释放，避免侧栏先就绪时露出SSR占位。
                if (!this.analysisReady && ['symbol-copy-text', 'watchlist-current-icon'].includes(host.id)) continue;
                shadow.replaceChildren(document.createElement('slot'));
                shadows.delete(host);
            }
        },
        accept(value, source) {
            const next = preview(value);
            if (this.preview && next.revision < this.preview.revision) return;
            this.preview = next;
            this.source = source;
            try {
                const raw = JSON.stringify(next);
                if (raw.length <= LIMIT / 2) localStorage.setItem(KEY, raw);
                else localStorage.removeItem(KEY);
            } catch { /* 只读缓存失败不能影响服务器数据。 */ }
            render();
        },
        loadFresh() {
            if (!this.fresh) this.fresh = read('/api/watchlists?cache=refresh').then((value) => {
                this.accept(value, 'data');
                return value;
            });
            return this.fresh;
        },
    };
    function shadowFor(id) {
        const host = document.getElementById(id);
        if (!host) return null;
        let shadow = shadows.get(host);
        if (!shadow) {
            shadow = host.shadowRoot || host.attachShadow({ mode: 'open' });
            shadows.set(host, shadow);
        }
        return shadow;
    }
    function text(id, value) {
        const shadow = shadowFor(id);
        if (shadow) shadow.textContent = value;
    }
    function paintHeader(symbol) {
        if (!symbol) return;
        const member = boot.preview?.snapshot.memberships.find(item => item.symbol === symbol);
        text('symbol-copy-text', `${symbol.slice(3)} ${member?.name || ''}`.trim());
        const shadow = shadowFor('watchlist-current-icon');
        const filled = boot.preview?.snapshot.memberships.some(item => item.groupId === boot.groupId && item.symbol === symbol);
        const icon = document.getElementById(`watchlist-star-${filled ? 'filled' : 'outline'}-icon`)?.cloneNode(true);
        if (shadow && icon) {
            icon.removeAttribute('id');
            icon.setAttribute('aria-hidden', 'true');
            const css = document.createElement('style');
            css.textContent = `svg{display:block;width:18px;height:18px;color:${filled ? 'var(--amber)' : 'var(--text)'}}`;
            shadow.replaceChildren(css, icon);
        }
    }
    function render() {
        if (released || (!boot.preview && !boot.error)) return;
        const shadow = shadowFor('watchlist-first-paint');
        if (!shadow) return;
        if (!boot.preview) {
            const message = document.createElement('p');
            message.textContent = '自选缓存暂不可用，请恢复连接后刷新。';
            shadow.replaceChildren(message);
            text('watchlist-status', '本地与服务器缓存暂不可用');
            observer.disconnect();
            return;
        }
        let selected;
        try { selected = localStorage.getItem('wavequant.watchlists.selected.v1'); } catch { /* 默认分类。 */ }
        const snapshot = boot.preview.snapshot;
        const group = snapshot.groups.find((item) => item.id === (boot.groupId || selected)) || snapshot.groups[0];
        boot.groupId = group.id;
        const linked = new URLSearchParams(location.search).get('symbol');
        const linkedSymbol = /^(sh\.(60|68)\d{4}|sz\.(00|30)\d{4}|bj\.(43|82|83|87|88|92)\d{4})$/.test(linked || '') ? linked : null;
        // 同正式入口：显式链接优先，否则当前分类第一只；不把展示预选写成用户点击。
        const first = snapshot.memberships.filter(item => item.groupId === group.id).sort((a,b) => a.position-b.position)[0];
        const displayedSymbol = boot.pendingSymbol || linkedSymbol || first?.symbol;
        paintHeader(displayedSymbol);
        const groupShadow = shadowFor('watchlist-group-first-paint');
        if (groupShadow) {
            const css = document.createElement('style');
            css.textContent = document.getElementById('watchlist-layout').textContent;
            const label = document.createElement('label');
            label.textContent = '分类';
            const select = document.createElement('select');
            select.setAttribute('aria-label', '自选股分类');
            for (const item of snapshot.groups) {
                const option = document.createElement('option');
                option.value = item.id; option.textContent = `${item.name}（${snapshot.memberships.filter(member => member.groupId === item.id).length}）`;
                select.append(option);
            }
            select.value = group.id;
            select.addEventListener('change', () => {
                boot.groupId = select.value;
                try { localStorage.setItem('wavequant.watchlists.selected.v1', select.value); } catch { /* 偏好不影响选股。 */ }
                render();
            });
            label.append(select); groupShadow.replaceChildren(css, label);
        }
        const members = snapshot.memberships.filter((item) => item.groupId === group.id && boot.query.trim().toLowerCase().split(/\s+/).every((token) => `${item.name} ${item.symbol}`.toLowerCase().includes(token)))
            .slice().sort((left, right) => left.position - right.position);
        const style = document.createElement('style');
        style.textContent = document.getElementById('watchlist-layout').textContent;
        const list = document.createElement('div');
        list.className = 'watchlist-preview-list';
        for (const member of members) {
            const row = document.createElement('div');
            row.className = 'watchlist-stock-row';
            row.dataset.selected = String(displayedSymbol === member.symbol);
            const drag = document.createElement('span');
            drag.className = 'watchlist-stock-drag';
            drag.setAttribute('aria-hidden', 'true');
            drag.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 5m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0 M9 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0 M9 19m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0 M15 5m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0 M15 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0 M15 19m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/></svg>';
            const name = document.createElement('strong');
            name.textContent = member.name || member.symbol;
            const code = document.createElement('small');
            code.textContent = member.symbol;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'watchlist-stock-open';
            button.dataset.symbol = member.symbol;
            button.setAttribute('aria-pressed', String(displayedSymbol === member.symbol));
            button.addEventListener('click', () => boot.select(member.symbol));
            const identity = document.createElement('span');
            identity.className = 'watchlist-stock-identity';
            const badge = document.createElement('span');
            badge.className = 'watchlist-backtest-badge';
            badge.textContent = '状态待确认';
            identity.append(name, code, badge);
            button.append(identity);
            row.append(drag, button);
            list.append(row);
        }
        if (!members.length) {
            const empty = document.createElement('p');
            empty.textContent = '当前分类暂无自选股';
            list.append(empty);
        }
        shadow.replaceChildren(style, list);
        text('watchlist-count', `${members.length} 只`);
        const chosen = snapshot.memberships.find((member) => member.symbol === boot.pendingSymbol);
        text('watchlist-status', chosen ? `已选中 ${chosen.name || chosen.symbol}，正在准备图表…` :
            boot.source === 'data' ? '点击股票切换；自选数据已同步' : '点击股票切换；后台同步自选数据中…');
        text('watchlist-backtest-status', '等待可用行情和回测参数…');
        if (!mounted) {
            performance.mark?.('wavequant:watchlist-preview-visible');
            performance.mark?.('wavequant:watchlists-interactive');
        }
        mounted = true;
        if (document.getElementById('symbol-copy-text') && document.getElementById('watchlist-current-icon')) observer.disconnect();
    }
    document.addEventListener('input', (event) => {
        if (!released && event.target?.id === 'header-stock-search') {
            boot.query = event.target.value;
            render();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (!released && event.target?.id === 'header-stock-search' && event.key === 'Enter') {
            const first = document.getElementById('watchlist-first-paint')?.shadowRoot?.querySelector('button[data-symbol]');
            if (first) { event.preventDefault(); boot.select(first.dataset.symbol); }
        }
    });
    const observer = new MutationObserver(render);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    render();
    document.addEventListener('DOMContentLoaded', () => {
        render();
        if (mounted || !document.getElementById('watchlist-first-paint')) observer.disconnect();
    }, { once: true });
    async function read(path) {
        const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw Error('watchlist read failed');
        const value = await response.json();
        preview(value);
        return value;
    }
    // 首屏缓存读取不等待hydration；回源Promise供后续交互运行时复用。
    boot.cached = read('/api/watchlists?cache=only').then((value) => boot.accept(value, 'server'))
        .catch(() => boot.loadFresh()).catch(() => {
            boot.error = true;
            render();
        });
})();
