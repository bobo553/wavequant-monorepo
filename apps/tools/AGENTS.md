# 工具型应用目录规则

本文件适用于 `apps/tools/*`，继承根 `AGENTS.md`。

- 浏览器扩展、CLI、桌面辅助程序和开发者工具放入 `apps/tools/<workspace>`。
- `apps/tools/create-project` 是仓库模板脚手架；新增模板类型或生成规则时必须验证路径边界、冲突拒绝、排除项、字段改写和 dry-run。
- 每个工具 workspace 提供适用的 `build`、`lint`、`typecheck` 和 `test:unit` 脚本。
- 工具源码、配置、测试和文档留在自身 workspace；`dist`、压缩包、商店产物和本地证书不提交。
- 外部命令、文件、网页内容和消息均按不可信输入处理；文件写入、批量操作和权限变更需要清晰边界。
- 创建类 CLI 默认不得覆盖已有目录、自动安装依赖或执行模板中的脚本；先展示计划，失败时清理自身创建的临时目录。
- 浏览器扩展遵循 Manifest V3 和最小权限，禁止远程代码与内置秘密，跨上下文消息必须校验。
- 含 UI 的工具额外遵守 `docs/agent/frontend/rules.md`，共享原语优先复用设计系统。

验证目标 workspace 的 lint、typecheck、unit test 与 build；浏览器扩展还需在目标浏览器加载验证权限、background、content script、Console 和主要流程。
