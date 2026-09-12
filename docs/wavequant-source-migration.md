# WaveQuant 源项目完整迁移对照

## 验收基线

迁移基线是 `E:\WorkSpace\股票` 的当前未忽略工作树，而不是其中某次 Git 提交，也不是单独的全景市场原型。源工作树在 2026-09-11 的基线验证为：

- Python `unittest`：505 项通过。
- Web `node:test`：37 项通过。
- 原页面入口：`E:\WorkSpace\股票\web\index.html`。

`data`、`results`、`notes` 和 `output` 是本地数据、封存结果或生成物，不作为源码提交复制；运行时通过 API 的 `--root`、`--tdx-root` 等显式参数接入，避免把约 4.1 GB 本机数据写入 Git。

## 迁移映射

| 源项目                                   | Monorepo 目标                                             | 保留能力                                              |
| ---------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------- |
| `wavequant/*.py`                         | `packages/wavequant-core/src/wavequant`                   | 研究、策略、回测、数据、纸面执行、运维和 CLI 领域能力 |
| `tests/test_visualization.py`            | `apps/servers/wavequant-api/tests/test_server.py`         | 只读 HTTP、同源安全、可视化仓库和扫描任务契约         |
| 其余 `tests/test_*.py`                   | `packages/wavequant-core/tests`                           | 483 项框架无关核心契约                                |
| `configs`、`docs`、`examples`、`scripts` | `packages/wavequant-core` 同名目录                        | 协议、证据、示例和离线校验工具                        |
| `web/index.html`                         | `apps/webs/wavequant-web/src/features/research-workbench` | React 主控研究工作台页面结构                          |
| `web/*.js`、`web/styles.css`             | `apps/webs/wavequant-web/public`                          | 原页面脚本、图表、标注、扫描、回测和样式              |
| `web/tests/*.test.mjs`                   | `apps/webs/wavequant-web/tests`                           | 37 项原 Web 行为契约                                  |
| 原 `visualization.py` HTTP Server        | `apps/servers/wavequant-api`                              | HTTP 适配器与 Core 解耦，并增加 MySQL/Redis 基础设施  |

Core 比源目录多出的 `project_paths.py` 只负责 `src` 布局下的仓库路径适配。源可视化测试中的网络绑定用例在 API 中按拆分后的 Web 构建边界重命名；其余源测试名称均能在 Core 或 API 找到对应契约。

## 页面路由

- `/`：默认进入原 `web` 主控研究工作台。
- `/research`：React 工作台的稳定直达入口。
- `/market`：后续新增的 Next.js 全景市场看盘，不覆盖原页面。
- `/wavequant-v2-classic.html`：全景市场 v2 原型兼容入口。

`pnpm --filter wavequant-web dev` 会同时启动 Next.js 与只读 API，并将 `/api/*` 同源代理到 API；无需再分别启动或手工拼接静态页面地址。

在当前迁移机器上复用源项目约 4.1 GB 的封存结果与通达信行情，可从 monorepo 根目录执行：

```powershell
pnpm --filter wavequant-web dev
```

`wavequant-api` 将 `wavequant-core[tdx]` 作为必需运行依赖，因此现场复权回测所需的 `pytdx` 与 `pandas` 会随 API 环境安装，不再依赖源项目虚拟环境。
