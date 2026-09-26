# 通用工程规范

本文件保存全仓通用编码、共享包、目录和质量规则，由根目录 `AGENTS.md` 按任务类型路由。分类目录和专项入口见 `docs/agent/README.md`；不要无差别加载全部规则。

## 读取路由

| 任务类型                             | 额外读取文件                                   |
| ------------------------------------ | ---------------------------------------------- |
| 任意代码实现                         | 本文件“通用编码规则”及任务相关章节             |
| 调试、测试设计、评审、重构或技术文档 | `docs/agent/general/quality-rules.md`          |
| 新增/升级依赖、许可证或供应链审查    | `docs/agent/general/quality-rules.md` 对应章节 |

专项文件只在任务命中时加载；提交和发布仍需遵守本文件的版本管理门禁。

## 通用编码规则

### 命名与类型

- 变量、函数、类、组件和文件使用有意义的英文名称；避免 `i`、`x` 等无法表达意图的命名，但短作用域中的行业惯例（如坐标 `x/y`）可以使用。
- 优先利用 TypeScript 推断；公共 API、跨层边界和复杂返回值应显式声明类型。
- `any` 只能用于无法避免的第三方兼容边界，并必须局部隔离且说明原因。
- 外部输入、反序列化结果和异常值使用 `unknown`，经过 Zod、类型守卫或模式匹配后再使用。
- 禁止用 `@ts-ignore`、无范围的 `eslint-disable` 或强制类型断言掩盖真实问题。

### 可读性与注释

- 函数和组件保持单一职责；复杂分支优先拆分为有业务含义的小单元。
- 注释用于解释决策、兼容性原因、算法不变量或外部限制，不复述代码。
- 公共 API 可使用 TSDoc；临时 TODO 必须说明原因或关联任务，不能成为永久占位。
- 修改旧代码时遵守“童子军规则”，但只清理本次改动直接触及的局部，不扩大任务范围。

### 依赖与导入

- JavaScript/TypeScript 内部包统一使用 `workspace:*`，包管理器统一使用 pnpm；Python 依赖与环境规则见 `docs/agent/python/rules.md`。
- 新增依赖前搜索仓库已有能力；运行时依赖和开发依赖放入正确区域。
- Barrel 文件只用于稳定公共 API。Web React 组件入口遵守 `docs/agent/frontend/rules.md`；其他单文件模块不机械创建 Barrel。
- 跨功能导入只能经过该功能的公开入口，避免依赖内部目录。

## 共享包规则

### `@repo/contracts`

跨 Web、Mobile、API 的 Zod Schema 和推导类型以该包为唯一事实来源。

| 内容          | 命名示例                          |
| ------------- | --------------------------------- |
| Zod Schema    | `CreateUserSchema`、`UserSchema`  |
| 输入/输出类型 | `TCreateUserInput`、`TUserOutput` |

新增契约时：

1. 在 `src/modules/<module>/<module>.schema.ts` 定义 Schema。
2. 从模块 `index.ts` 和包根 `src/index.ts` 导出。
3. 如需子路径导入，同步更新 `tsup.config.ts` 和 `package.json#exports`。
4. API DTO 从契约类型推导，前端表单直接复用同一 Schema。
5. 运行 contracts 构建及所有消费者的类型检查。

### 设计系统

- Web 原语来自 `@repo/design-system-web`，Mobile 原语来自 `@repo/design-system-mobile`。
- 浏览器 Web（包括 H5、官网、门户、业务应用和 Admin）使用的 shadcn/ui 原语必须在 `packages/design-system/web` 内生成、封装和维护，各应用只通过 `@repo/design-system-web` 使用，禁止在应用内保留另一份 `components/ui`；具体页面与组件约束见 `docs/agent/frontend/rules.md`，桌面页面再读 `docs/agent/frontend/pc-web-rules.md`。
- 无业务逻辑且被多个页面/功能复用的组件放入设计系统；含导航、接口或领域逻辑的组合组件留在应用 `shared/components` 或功能目录。
- Mobile 主题事实来源是 `packages/design-system/mobile/src/theme.ts`；Web CSS 变量与移动端语义 Token 需要保持一致，但不要求实现形式相同。
- 图标使用项目既有图标库。SVG 图标的大小和颜色通过组件属性传入，避免假设 NativeWind 类能作用于所有图标实现。

### `@repo/env`

- 新环境变量先加入 `packages/env/src/env.schema.ts`，再更新 `.env.example` 和使用方文档。
- 服务端秘密不得暴露为 `NEXT_PUBLIC_*` 或 `EXPO_PUBLIC_*`。
- 只在需要的服务首次访问时进行惰性校验，不能让某个应用因另一个应用专属变量缺失而启动失败。
- 不提交任何 `.env.*` 实值文件。

## 应用目录规则

应用级规则按目标文件所在目录就近加载，避免把所有技术栈细节塞入仓库级上下文：

| 目录             | 职责范围                                | 规则入口                 |
| ---------------- | --------------------------------------- | ------------------------ |
| `apps/servers/*` | 服务端、API、任务和后台进程             | `apps/servers/AGENTS.md` |
| `apps/mobiles/*` | C 端、移动端项目与页面                  | `apps/mobiles/AGENTS.md` |
| `apps/webs/*`    | H5、浏览器业务应用与 Admin              | `apps/webs/AGENTS.md`    |
| `apps/tools/*`   | 浏览器扩展、CLI、桌面辅助工具等独立应用 | `apps/tools/AGENTS.md`   |

- 根目录 `AGENTS.md` 决定全局安全、范围、状态和验证门禁；应用级规则只能增加约束。
- 新建运行应用时先按职责选择一级目录，再使用 `apps/<类型>/<workspace>` 结构；不得恢复 `apps/<workspace>` 扁平布局。
- 同一功能跨多个应用时，以 `@repo/contracts` 固化接口，分别遵守各目录规则并验证生产者和消费者。
- Python 项目仍按职责进入 `servers`、`tools` 或 `packages`，并额外加载 `docs/agent/python/rules.md`；不要按语言另建顶级目录。

## 质量、测试与版本管理

### 验证层级

1. 修改文件的格式检查。
2. 受影响工作区的 lint 与类型检查。
3. 实际提交代码前运行自动化测试；日常修改后暂不运行，并记录待验证项。
4. 跨包改动的消费者构建或不含测试的全仓快速门禁 `pnpm verify:quick`。
5. 完整 `pnpm verify` 含自动化测试，仅在实际提交代码前执行。

测试文件约定：

| 后缀           | 用途             | 工具                    |
| -------------- | ---------------- | ----------------------- |
| `*.test.ts(x)` | 单元/组件测试    | Vitest、Testing Library |
| `*.spec.ts(x)` | 浏览器端到端测试 | Playwright              |

### 提交与发布

- 提交信息遵循 Conventional Commits：`feat`、`fix`、`chore`、`docs`、`refactor`、`test`、`style`、`perf`、`ci`。
- 修改可发布包时评估是否需要 Changeset；纯内部文档/Harness 变更通常不需要。
- 智能体未经用户明确要求不得提交、推送、创建 PR、发布包或部署。
- 提交前确认没有真实环境变量、凭据、构建产物或无关文件进入变更集。
