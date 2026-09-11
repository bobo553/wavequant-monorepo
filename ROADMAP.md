# 项目路线图

本文面向项目用户、贡献者和维护者，公开说明 monorepo 模板的发展方向。为避免状态冲突，本文不会重复维护 [`feature_list.json`](./feature_list.json) 中的执行信息。

> 路线图表达的是方向，而不是交付承诺。随着模板、技术生态和贡献者需求变化，范围与优先级可能调整。

| 元数据       | 内容                   |
| ------------ | ---------------------- |
| 负责人       | 仓库维护者             |
| 受众         | 用户、贡献者和维护者   |
| 适用范围     | 产品方向与里程碑级意图 |
| 最后审查日期 | 2026-09-11             |

## 项目方向

本项目致力于提供一套面向生产的 monorepo 基础模板：便于采用、适合编码智能体安全修改，并在 H5、Admin、API 和 Mobile 应用之间保持一致。

路线图决策遵循以下原则：

1. 优先建设可复用的平台能力，避免添加一次性演示功能。
2. 保证生成的 workspace 可以独立运行、测试和部署。
3. 在不同应用之间保持严格的数据契约、环境边界和共享设计原语。
4. 只有通过可执行验证，才将实现视为已交付。

## 如何阅读

| 阶段           | 含义                           | 与执行状态的关系                                        |
| -------------- | ------------------------------ | ------------------------------------------------------- |
| 现在（Now）    | 已批准且正在交付的工作         | 必须引用 `feature_list.json` 中当前活动的 Feature ID    |
| 下一步（Next） | 已排定优先级、等待细化的候选项 | 只有范围和验收条件获批后，才会取得 Feature ID           |
| 未来（Later）  | 值得保留但尚未承诺范围的方向   | 不得视为待办任务或交付承诺                              |
| 已交付基础     | 当前已经具备的代表性能力       | 引用已完成的 Feature ID；完整历史仍以 Feature List 为准 |

精确状态、依赖关系、验收条件和验证证据只保存在 [`feature_list.json`](./feature_list.json) 中。只有维护者作出明确排期承诺时才会增加日期，并且日期只在一个权威计划系统中维护。

## 现在（Now）

当前没有已批准且正在交付的工作。精确状态以 [`feature_list.json`](./feature_list.json) 为准。

## 下一步（Next）

以下候选方向按预期平台价值排序，目前均不是已承诺功能。

### 模板升级工作流

探索一种安全机制，用于识别并应用模板后续改进，同时避免覆盖已生成 workspace 中的业务代码。

### 全栈参考链路

设计一个可选、可替换的参考示例，展示由共享契约驱动、贯穿 API 与部分客户端的完整流程，同时确保业务逻辑不进入共享包。

### 发布可信度

增强生成 workspace、容器配置和发布元数据的冒烟验证，让模板变更尽早失败并提供可操作的诊断信息。

## 未来（Later）

- 只有重复出现真实需求、足以覆盖维护成本时，才新增应用模板。
- 当 Issue 数量需要交互式筛选、负责人和排期视图时，再评估建立公开的 GitHub Projects 路线图。
- 完善主要框架和运行时升级的迁移指南，但不把模板发展成版本管理服务。

## 已交付基础

- **智能体友好的 monorepo 结构：**[`MONOREPO-001`](./feature_list.json)、[`MONOREPO-002`](./feature_list.json) 和 [`MONOREPO-005`](./feature_list.json)。
- **Web 与多端应用模板：**[`MONOREPO-004`](./feature_list.json)、[`MONOREPO-006`](./feature_list.json) 和 [`MONOREPO-008`](./feature_list.json)。
- **仓库发布与项目创建工具：**[`MONOREPO-003`](./feature_list.json) 和 [`MONOREPO-007`](./feature_list.json)。
- **公开计划边界与路线图自动校验：**[`MONOREPO-009`](./feature_list.json)。
- **WaveQuant 量化研究工作台、Web/API/Core 边界及 SQL/Redis 基础设施：**[`MONOREPO-010`](./feature_list.json)、[`MONOREPO-011`](./feature_list.json) 和 [`MONOREPO-012`](./feature_list.json)。

## 贡献与推进流程

1. 通过 GitHub Issue 或维护者评审讨论提案。
2. 早期想法保留在“下一步”或“未来”，不得描述为已排期工作。
3. 范围获批后，在 `feature_list.json` 中创建包含依赖关系和验收条件的 Feature。
4. 每次只将一个选定 Feature 标记为 `in-progress`；仓库同一时间只允许一个活动 Feature。
5. 只有全部验证证据通过后，才能将 Feature 标记为 `done`，并把有公开价值的成果汇总到“已交付基础”。

当路线图描述与 Feature 状态不一致时，以 [`feature_list.json`](./feature_list.json) 为准。
