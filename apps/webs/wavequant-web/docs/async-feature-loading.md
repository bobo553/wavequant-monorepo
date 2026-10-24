# 前端异步功能边界

2026-10-24 · MONOREPO-362 · @repo/async-ui 首个 WaveQuant 业务接入。

## 加载划分

| 区域 | 首次加载时机 | 资源与所有权 |
| --- | --- | --- |
| 自选、当前K线、导航及轻量反馈 | 研究首屏 | 保留直接响应；自选不依赖研究配置 |
| 市场九个Tab | 该Tab实际渲染 | async-market-views分别注册defineAsyncView，不静态导入所有视图 |
| 市场广度图表 | 总览图表渲染 | ECharts独立loader；切换离开后沿用原组件销毁逻辑 |
| 市场阶梯 | 阶梯Tab或总览可见阶梯预览 | 共用一个异步视图，预览本身可见时会加载，并非后台预取 |
| 搜索/个股/设置/通知/说明弹框 | 当前受控dialog被打开 | 五个正文分别defineAsyncView；外壳和toast轻量直接渲染 |
| 研究绩效/委托/健康 | URL首次选择相应页 | 首次进入前没有页面DOM，进入后保留节点供原生运行时复用 |
| 策略拓扑/画布 | 首次进入拓扑及画布挂载 | 拓扑数据与Tldraw分别注册异步视图，之后保留编辑状态 |
| 绩效图表实现及三个实例 | 首次进入绩效且DOM就绪 | 原生import复用同一charts.js主题基元，实例缓存；数据到达和重新进入时更新 |
| 右侧买点/结构查询工具 | 展示对应工具；默认买点属于可见内容 | 原生ESM通过LoadController桥接，加载本身不启动查询 |
| 幅度方案对比 | 首次有效点击 | ratioPlans轻量元数据独立；模块就绪后补执行首次操作，其他点击交由原类处理 |

## 状态与错误

动态loader在注册时不执行；模块Promise复用、加载超时和失败缓存使用async-ui。AsyncFeatureFallback提供稳定最小高度、加载/错误提示及显式reset重试；渲染异常由AsyncFeatureBoundary限定在当前内容内。没有全局预热所有Tab、hover自动拉取或服务启动批量加载重型组件。

全局弹框维持现有单一dialog状态和唯一外壳，不额外引入第二套队列状态。关闭或切换类型后，迟到的模块只进入代码缓存，不重新打开旧弹框。外壳负责遮罩、Escape、Tab边界和焦点恢复；toast不会引入搜索样本、设置或个股正文。

市场视图沿用原先切出即卸载的生命周期，轮询/图表清理仍由视图负责；跨Tab偏好在MarketWorkspaceProvider中。研究页保留已访问节点是因为原生图表和表格持有DOM引用；页面提交后发research-section-ready，不把import结束误当作DOM已存在。showPage同步URL并响应popstate，初始化不覆盖用户在加载期间的Tab选择。健康接口合并同运行ID并发请求。

原生低频工具的LoadController在ResearchRuntime中安装；结构/幅度工具不因contextChanged调用而下载代码，首次操作在加载后再次检查有效性。行情、趋势识别、交易判定与SDK逻辑不变。

## 依赖与运行

package.json使用@repo/async-ui workspace:*，pnpm-lock仅增加该本地链接。离线锁解析遇到无关Playwright元数据缺失，未下载或升级外部包。C工作树async-ui的node_modules复用本机既有依赖，构建启动依赖产物；专属运行目录复制dist并alias本工作树版本，避免读取main旧产物。async-ui库源码未修改。

最后前端PID56680/3003，API71344/8010保持运行，研究HTML启动预热结束。日志位于.codex-runtime/rust-startup/build-async-ui-startup.log和frontend-indexed.stdout.log。启动编译/预热不是业务验证。

## 待提交阶段验证

新增deferred-research-pages.test.tsx覆盖未访问DOM缺席、提交通知、切换后节点保留；deferred-market-overlay.test.tsx覆盖toast独立、正文加载中关闭及重新打开。市场交互回归改为等待异步正文，原生年线夹具补loader桥接。均未执行，不引用以前通过结果。

本轮未运行浏览器、Playwright/E2E、单元套件、lint、格式、专项类型、产品构建或性能探针。待验证包括：九Tab/五类弹框首开与重开、快速切换和迟到响应、首次操作不丢失、失败重试、键盘/焦点/窄屏、初始包和异步chunk大小、首屏和交互时延。不能凭新增import边界宣称实际产物大小或耗时已下降。当前生产build使用Turbopack，不宣称Webpack清单/预算插件已覆盖；未为此改动CI或构建门禁。


## 2026-10-24 实际浏览器审计补充

2026-10-24 MONOREPO-362 前端JS性能审计：按用户明确要求使用Chrome调试及必要生产构建。3003从next dev切为next start，public原生ESM由esbuild0.28.0合并并保留动态import边界，自选/主运行时共用单例，删除两条零散模块preload；哈希分块长期immutable缓存，稳定入口保持可更新。移除error设计系统整桶依赖；经典HTML链接禁用自动预取；拓扑默认轻量规则预览，高级Tldraw画布点击加载。冷缓存同页本次生产合并前87个JS/68个小于10KiB/455912B传输，合并后22/11/408871B；剩余小文件合计20637B传输，其中5个Next启动路由错误边界、6个原生入口共享与按需买点。单次对比自选交互450→371ms、完整运行时944→903ms，最终独立上下文342/725ms；时间存在波动，不能当长期保证。合并后解压体积1300269→1349140B略增，未把请求减少当作所有指标下降。HTTP/1.1 fetchStart→requestStart累计1630→363ms是并发请求累计值，非页面节省秒数。首屏无绩效/结构/幅度/Tldraw下载；切绩效后按需模块正常；拓扑默认无>1MB脚本；九市场Tab切换、通知及设置弹框可用，目录无自动请求，最终Console无error/warn。构建期间曾在旧页面出现临时chunk500，刷新最新构建后消失；下次启动改用独立轮换构建目录防止覆盖在服资源。用户提供content_main.js完整URL证实属于沉浸式翻译扩展，不计本站HTTP传输。前端PID72280/3003，Rust71344/8010未变。未运行Playwright/E2E、单元套件、lint、格式或额外类型检查；生产Next构建含类型编译完成。新增serve独立暂存启动入口未单独运行，完整弹框/快速切换与长期性能仍待后续提交验证。未提交合并推送；Python及量化交易逻辑未改，整体in-progress。

生产预览可使用 `pnpm --filter wavequant-web serve`：构建源码副本及原生模块到 `.codex-runtime/production-<pid>` 后运行next start，不覆盖开发源码。此前PID56680及未执行浏览器的说明是上一阶段历史；本次以本文新增实测为准。构建依赖esbuild已固定0.28.0及锁文件，未升级其他包。esbuild code splitting参考 https://esbuild.github.io/api/#splitting 。开发模式的大JS不可直接作为生产包证据，Network保留日志会累计多次导航；比较应使用新上下文同一页面，区分传输字节与解压字节。
