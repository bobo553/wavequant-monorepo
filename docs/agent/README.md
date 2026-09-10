# 智能体规则目录

本目录保存仓库的中文工程规范。根 `AGENTS.md` 是唯一全局入口；本文件只负责分类索引和上下文路由，不要求智能体一次读取全部规则。

## 分类索引

| 分类     | 入口                      | 触发场景                                                     |
| -------- | ------------------------- | ------------------------------------------------------------ |
| 通用     | `general/rules.md`        | 任意代码实现、调试、测试、评审、共享包、文档、依赖与版本管理 |
| 前端     | `frontend/rules.md`       | PC Web、Admin、Mobile、移动 H5、React、UI 和浏览器扩展界面   |
| 后端     | `backend/rules.md`        | NestJS、API、Worker、任务和服务端适配器                      |
| Python   | `python/rules.md`         | Python 源码、服务、Worker、CLI、库、自动化脚本与工具链       |
| 架构     | `architecture/rules.md`   | 系统设计、应用/服务拆分、分布式协作、容量、可用性和技术选型  |
| 数据数仓 | `data-warehouse/rules.md` | 埋点、ETL/ELT、指标、治理、分析、可视化和实验                |
| 算法     | `algorithms/rules.md`     | 复杂算法、数据结构、数值计算、搜索/排序、随机过程和性能优化  |
| 运维     | `operations/rules.md`     | CI/CD、容器、IaC、IAM、网络、可观测、事故、发布和灾备        |
| AI       | `ai/rules.md`             | 模型、Prompt、上下文、RAG、Agent、工具、微调、多模态和评测   |

## 目录结构

```text
docs/agent/
├── README.md
├── general/
│   ├── rules.md
│   └── quality-rules.md
├── frontend/
│   ├── rules.md
│   └── pc-web-rules.md
├── backend/
│   ├── rules.md
│   ├── nestjs-rules.md
│   ├── architecture-rules.md
│   ├── api-rules.md
│   ├── data-rules.md
│   ├── reliability-rules.md
│   └── security-rules.md
├── python/
│   ├── rules.md
│   ├── testing-rules.md
│   └── packaging-rules.md
├── architecture/
│   ├── rules.md
│   ├── system-design-rules.md
│   └── distributed-systems-rules.md
├── data-warehouse/
│   ├── rules.md
│   ├── tracking-rules.md
│   ├── modeling-governance-rules.md
│   └── analytics-experiment-rules.md
├── algorithms/
│   └── rules.md
├── operations/
│   ├── rules.md
│   ├── delivery-rules.md
│   ├── infrastructure-rules.md
│   └── observability-incident-rules.md
└── ai/
    ├── rules.md
    ├── prompt-context-rules.md
    ├── rag-retrieval-rules.md
    ├── agent-tool-rules.md
    └── model-delivery-rules.md
```

## 加载规则

1. 所有代码任务先读取 `general/rules.md` 中与任务匹配的章节；调试、测试策略、评审、重构、文档或依赖任务再读取质量专项规则。
2. 再读取一个主领域的 `rules.md`，按其“读取路由”选择最多必要的专项文件；只有任务跨领域时才组合多个入口。
3. 后端任务先按 `backend/rules.md` 的表格选择 NestJS、架构、API、数据、可靠性或安全专项文件；任意服务端实现加载 NestJS 专项，后端架构专项再按需要路由到通用系统设计、分布式或运维规则。
4. `apps/webs/*` 先加载前端通用规则；Admin、桌面门户和数据工作台再加载 `frontend/pc-web-rules.md`，H5 不因位于 Web 目录自动套用 PC 布局规则。
5. Python 任务先读 `python/rules.md`；涉及 pytest 或打包发布时，再分别加载测试或打包专项，不因使用 Python 自动加载后端规则。
6. 目录最近的 `AGENTS.md` 可以增加项目事实与专项门禁，但不能放宽根规则。
7. 历史记录中的路径不作为路由依据，以根 `AGENTS.md` 和本索引的当前路径为准。

不要因“可能有用”加载全部目录。架构、运维、数仓、算法、Python 专项和 AI 规则只在任务真正涉及相应决策或实现时读取。

## 维护约定

- 分类目录使用英文 kebab-case，分类入口统一命名为 `rules.md`，专项规则使用 `<topic>-rules.md`。
- 规则内容使用简体中文，代码标识符、协议和第三方产品名保留英文。
- 新规则先放入最窄且唯一的领域；跨领域规则保留一个事实来源，其他文件只建立引用。
- 单个入口只保留领域边界、核心不变量和路由；触发条件独立的细则拆成专项文件，并同步本索引与根路由。
- 外部知识只提炼稳定、可执行和可验证的约束；不复制教程、固定规模阈值、厂商排名、绝对性能数字或易过时版本参数。
- 路径迁移必须全仓检索旧引用，更新 Harness 状态，并运行 Prettier、链接/结构检查和 `pnpm harness:check`。
