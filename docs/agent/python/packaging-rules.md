# Python 打包与依赖规范

本文件仅在新建 Python 项目、调整 `pyproject.toml`、构建 wheel/sdist、发布库、定义 CLI 或管理依赖时加载。

## `pyproject.toml` 与布局

- `pyproject.toml` 是项目元数据、构建后端、Python 版本、依赖和工具配置的事实来源；新增项目不得以 `setup.py` 的命令执行方式作为主要构建流程。
- 可发布项目使用标准 `[build-system]` 和 `[project]` 元数据；名称、版本来源、README、许可证、作者、分类、URL 与 `requires-python` 必须准确且可验证。
- 应用和库优先采用 `src/<import_package>/` 布局，分发名可用连字符，导入包名使用合法的 `snake_case`；不要假设两者必须完全相同。
- 普通模块导入不得依赖仓库根为当前目录。构建和测试应针对已安装项目，发现导入路径问题时修正包配置而不是注入 `PYTHONPATH`。
- 单仓多 Python 项目各自保留清晰 `pyproject.toml`；共享配置只能在工具原生支持继承且不会模糊发布边界时集中。

## 版本与依赖

- 运行时依赖放入 `[project].dependencies`，可选用户能力放入 `[project.optional-dependencies]`；仅开发、测试、文档和发布所需依赖使用项目工具支持的开发依赖组。
- 直接依赖必须显式声明，不能依赖传递包恰好存在。按实际兼容范围设置上下界，避免无证据的精确锁死或无限制依赖。
- 应用、服务和工具提交单一受支持的锁文件，并在 CI 使用冻结/不可变模式；可发布库以元数据表达兼容范围，锁文件只用于开发与测试复现。
- 不手工维护由锁定、导出或代码生成工具产生的文件；在文档中记录权威输入和重建命令，生成后检查 diff。
- Git、路径和本地 editable 依赖不得进入公开发布元数据；仓库内 Python 库应通过明确 workspace/path 协议开发，并验证生成产物不泄漏本机绝对路径。
- 新依赖先检查维护状态、许可证、发布来源、二进制平台支持、类型信息、安全记录和替代方案；安装、构建和发布过程遵守通用供应链规则。

## 公共 API 与兼容性

- 使用 `__all__`、稳定导入路径和文档明确公共 API；以下划线开头的模块/名称视为内部，但不能用命名替代真正的兼容策略。
- 遵循语义化版本和仓库发布流程。删除、重命名、参数语义、异常类型、序列化格式或 CLI 退出码变化都需要评估兼容性与迁移窗口。
- 不在导入时探测网络、读取用户配置或执行迁移；可选依赖缺失时在对应能力首次使用处给出清晰、可操作的错误。
- 类型标注属于库契约；发布 typed package 时包含 `py.typed` 或正确 stub 文件，并从构建产物验证它们确实被打包。

## CLI、资源与构建产物

- CLI 使用 `[project.scripts]` 等标准入口指向可导入函数；解析参数、领域逻辑和进程退出映射分离，便于单元测试。
- CLI 退出码稳定，正常输出与诊断输出分离，非交互环境不强制颜色或提示；破坏性命令提供显式确认或 dry-run，并遵守仓库授权边界。
- 包内模板、Schema 和静态资源通过标准资源 API 读取并显式纳入构建，不依赖源码树相对路径。
- 发布前同时构建 wheel 和 sdist，在干净环境安装 wheel 后验证导入、CLI、元数据和最小测试；不要只测试 editable install。
- 构建必须从干净源码可复现，`dist/`、`build/`、`*.egg-info` 等产物不提交；产物关联源码提交、构建环境和摘要。

## 发布门禁

按项目工具执行等价检查：

```bash
mypy src tests
pytest
python -m build
```

- Python 代码按 `docs/agent/python/rules.md` 直接写成符合项目规范的格式。
- 对生成的 wheel/sdist 运行元数据检查，并在临时虚拟环境 smoke test；发布前确认包名、版本和目标索引，避免覆盖或误发正式仓库。
- 发布凭据使用短期、最小权限的可信发布机制或秘密管理，不写入命令历史、配置文件、日志和 CI 产物。
- 发布、撤回、删除版本或修改远端索引属于外部高影响操作，必须获得用户明确授权；本规则不构成发布授权。

## 官方依据

- [PyPA：Writing your `pyproject.toml`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [PyPA：`src` layout 与 flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [PyPA：Packaging Python Projects](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [Python entry points specification](https://packaging.python.org/en/latest/specifications/entry-points/)
