# 自选股首屏缓存顺序

## 已确认的代码等待关系

原本的本地预览位于 ResearchRuntime.useEffect 之后，需要先完成 React hydration 和运行时依赖加载。即使 localStorage 命中，也不能提前显示列表。截图只能证明占位仍在，不能据此量化其中网络、编译或 hydration 各占多少。

## 当前读取路径

1. head 中内联的独立 watchlist-first-paint.js（不增加脚本下载请求） 在页面解析阶段读取 preview.v2；兼容旧 preview.v1。只包含 revision 与 snapshot，最多 256 KiB，按分类及 position 显示。没有缓存时不伪造空列表。
2. 同时请求 GET /api/watchlists?cache=only，读取按 SQLite 路径隔离、启动预热的进程内快照。此路径不打开 SQLite，不进入 SDK/量化计算门禁；结果是普通 JSON，首屏无需下载索引解码模块。
3. 缓存读取失败时立即回退 cache=refresh。正常情况下完整交互运行时在后台请求 cache=refresh，SQLite 读取使用独立的 4 路有界通道；原无参数 GET 仍保留索引协议。首次数据库 Promise 由首屏及完整运行时共享，避免重复读取。

自选小文档使用进程内缓存，无需新增 Redis 往返。增删、保存和迁移在 SQLite 提交后发布快照；普通数据库读取也更新缓存。发布比较 revision，迟到旧读不覆盖新版本。另一个进程修改数据库后，下一次 fresh 读取刷新内存；cache-only 仅作为可能滞后的预览。

## 绘制与一致性

首屏预览仅写入独立 Shadow DOM，不修改 React 拥有的 light DOM，避免 hydration 不匹配。交互列表渲染完成后释放预览、恢复原有状态文本。快照不包含可编辑状态，不作为提交 revision 的依据；服务器确认后才开放编辑。服务器缓存和数据库不可用时保留本地预览并提示，不清空列表。名称通过 textContent 输出。正常服务响应更新紧凑快照，旧 revision 不覆盖新版本。

performance.mark("wavequant:watchlist-preview-visible") 标记预览首次显示；当前仅新增标记，未采样或声称实际耗时。首次无本地缓存仍需至少一次服务器响应；HTML 冷编译、网络离线、浏览器禁止存储仍是实际边界。

## 本轮执行边界

新增首屏先于 React、服务器缓存回退/Promise 复用、空列表、旧版本保护及 Rust 提交后缓存刷新回归源码。开发阶段未运行测试、浏览器、性能探针、lint、格式、专项类型检查。仅在按既有约定重启服务时进行必要启动编译；这不代表首屏性能验收通过。
