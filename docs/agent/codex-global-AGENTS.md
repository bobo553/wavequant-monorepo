<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, follow the global project initialization rule below.
<!-- CODEGRAPH_END -->
## Default CodeGraph setup for projects

The user has chosen CodeGraph indexing as the default for every code project. At the start of work in a project, identify its project root. If the root has no `.codegraph/` directory, run `codegraph init --yes <project-root>` once before code exploration. If it is already initialized, use the existing index; do not rebuild it on every task. Use `codegraph sync` when the index needs updating. `codegraph install` configures the agent globally and is already done; do not repeat it in each repository. If initialization fails, report the specific error and continue the task with available tools.

## Reset credits

Never automatically redeem or consume a Codex usage reset credit (重置卡). Ask for the user's explicit confirmation for each individual credit before using it. Reaching a usage limit, continuing a task, or a previous confirmation does not authorize another redemption.

## 开发验证节奏

- 日常开发、调试和普通代码修改时，不运行 Playwright、浏览器端到端测试或启动 Playwright 浏览器；只有用户在当前任务中明确要求时才运行。仓库内较严格的默认 E2E/真实浏览器门禁不能代替用户的明确要求。
- 开发过程中优先运行与改动直接相关的定向测试、静态检查和必要构建。完整 Web 单测套件留到空闲时段或独立后台验证执行，不占用用户正在开发、调试的时间；如果本轮没有合适的空闲执行窗口，记录为待验证并明确告知，不阻塞当前交付。
- 用户明确要求立即进行完整验证时，按该次要求执行。

## Git 多任务开发与本地合并流程

- Git 代码开发默认使用独立 worktree。新建 Codex 任务且可选择运行环境时，优先选择 `Worktree`（或 `/worktree`），并选择目标基线分支；通过任务创建工具启动新任务时，指定 worktree 环境。若任务已经在 `Local` 启动，先创建独立 Git worktree，并把后续文件修改和 Git 命令放在该 worktree 中执行。用户明确要求直接操作当前检出时，遵从该次要求。非 Git 项目及只读问答不需要 worktree。`AGENTS.md` 只能约束执行流程，不能改变 Codex 界面的新任务默认选项。
- 适用于有 `main` 分支的 Git 项目中的代码开发任务。新建开发会话时，从最新的本地 `main` 为该任务建立独立的 Git worktree，特性分支命名为 `feat-中文名`（`feat-` 后使用简明中文任务名）；多个任务并行时，每个会话各用一个 worktree 和一个分支，不能共用工作目录或开发分支。不要在正在使用的主工作目录中为并行任务反复切换分支。
- 建立特性分支后，将 Codex 会话名称设为完整分支名；如果用户已手动修改会话名称，或之后手动改名，始终保留用户定义的名称，不再用分支名覆盖。
- 开始前确认仓库根目录、`main` 分支和工作区状态。保留已有未提交改动；若这些改动妨碍创建 worktree 或合并，先报告具体阻碍，不得擅自丢弃、重置或覆盖。若项目没有 `main`，先确认项目的实际主分支，不自行猜测替代基线。
- 在各自的特性分支完成实现、与改动直接相关的检查和代码复核，再提交该任务的改动。每条提交标题必须准确描述本次改动，整条标题少于 35 个字符（类型前缀、空格和标点也计入）。验证节奏遵循上文“开发验证节奏”；检查未通过时先修复，无法通过时说明原因，不合并。
- 各任务提交后，按顺序将特性分支合并到本地 `main`。合并每一个任务前，先让该分支同步当时最新的本地 `main`，处理冲突并重新运行受影响的检查；复核该分支相对于 `main` 的最终差异后，本地执行 `git merge --no-ff --no-edit` 生成合并提交。第二个及后续任务必须基于前一个任务合并后的 `main` 重复同步、冲突处理、验证和复核。
- 只有提交、检查和复核都完成且合并条件满足时才执行本地合并。合并后确认 `main` 状态，自动执行 `git push origin main`。推送失败时保留本地提交并报告原因，不强制推送或改写远端历史；最后报告分支、提交、检查、合并与推送结果。
