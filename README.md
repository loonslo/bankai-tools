# bankai-tools

个人轻量工具箱。一个仓库收纳独立小工具与 Skill，Python 3.14+，按需安装各工具的可选依赖。

| 入口 | 功能 |
| --- | --- |
| [clear-writing](skills/clear-writing/SKILL.md) | 中文起草与润色，保留原意和事实，减少套话，改善表达与段落衔接 |
| [privacy-review](skills/privacy-review/SKILL.md) | 自包含的隐私审核 Skill，检查指定文件、记忆／聊天导出、Git 暂存区和拟推送历史，返回脱敏建议 |
| [X 关系工具](tools/x-follow/README.md) | 调用 twitterapi.io 分页获取关注／粉丝，计算互关、我关注但未回关、仅关注我的账号，支持 JSON／CSV |
| [AI 配置同步](tools/ai-config-sync/README.md) | 共享规则与选定设置部署，选择性备份／恢复、本地事务及撤销 |

## 目录分类

```text
README.md               # 总入口与开发方式
skills/                 # 自包含 Skill
  clear-writing/
    docs/               # 本 Skill 的任务与验收记录
  privacy-review/
    tests/              # 本 Skill 的独立测试
    docs/               # 本 Skill 的来源与记录
tools/                  # 独立小工具
  ai-config-sync/        # 配置源与同步工具
    pyproject.toml      # 本工具的安装与检查配置
    common/
    run.py
    tests/
    docs/
  x-follow/
    pyproject.toml      # 本工具的安装与检查配置
    LICENSE             # X 工具的原 MIT 声明
    run.py              # 无需安装的运行入口
    bankai_tools/       # 工具代码
    README.md
    tests/              # 本工具的独立测试
    docs/               # 本工具的来源与记录
```

新增 Skill 放入 `skills/<skill-name>/`，新增小工具放入 `tools/<tool-name>/`。
`tools/ai-config-sync/` 与 `tools/x-follow/` 分别维护各自代码、配置源和运行入口。
测试和文档随各自工具或 Skill 维护，根目录不设 `tests/`、`docs/`。
各模块 `docs/tasks.md`、`docs/acceptance.md` 为本地记录，不提交 Git。

根目录仅保留总入口、本地 `AGENTS.md`、`PROJECT_STATUS.md` 及 Git 配置；安装配置和许可归所属工具。
源码以各模块内的代码目录为正本；`build/`、`dist/`、`*.egg-info/` 是安装或打包时
可能重新生成的目录，不在其中编辑代码，也不纳入 Git。
`.cache/` 是 pytest／Ruff 用于加速重复检查的可再生成缓存，可以清理，不影响工具运行。

## AI 配置同步

填写该目录的本机配置文件后，先预览再应用：

```powershell
py -3.14 -B tools/ai-config-sync/run.py --device tools/ai-config-sync/device.json sync
py -3.14 -B tools/ai-config-sync/run.py --device tools/ai-config-sync/device.json sync --apply
```

只部署规则时无需额外依赖；更新 Codex TOML 字段时使用 `tomlkit`。
选择性备份、恢复、冲突处理与撤销见 [使用说明](tools/ai-config-sync/README.md)。不写入真实配置的验证与实际跨机验收分开记录。

## X 关系工具

Python 标准库即可运行，无需安装。密钥通过进程环境变量 `TWITTERAPI_IO_KEY` 提供。
在仓库根目录运行：

```powershell
# 读取 API（供应商按请求计费）；每类最多 20 页，每页 200 人
py -3.14 -B tools/x-follow/run.py fetch example_user --max-pages 20 --output data/snapshot.json

# 用完整快照离线重算，无网络请求
py -3.14 -B tools/x-follow/run.py analyze data/snapshot.json --output data/relations.json
py -3.14 -B tools/x-follow/run.py analyze data/snapshot.json --format csv --output data/relations.csv
```

付费请求、分页上限、完整性及导出说明见 [工具说明](tools/x-follow/README.md)。
也可独立安装 X 工具后使用 `x-follow ...`，兼容保留 `bankai x-follow ...`；Skill 单独复制。

## 隐私审核 Skill

将 `skills/privacy-review/` 整个目录放入支持 Skill 的客户端目录，或直接运行其中脚本：

```powershell
py -3.14 -B skills/privacy-review/scripts/review.py files --repo "<待检查项目目录>" --paths README.md
```

本机需要 Gitleaks 8.x（本次验收为 8.30.1），Git 模式另需 Git。可用 `PRIVACY_GUARD_GITLEAKS`
指定扫描器绝对路径，不捆绑程序、不自动下载。只返回建议；发现风险仍返回 0，检查不完整返回 1。
为避免误读认证目录，审核引擎拒绝隐藏根目录及隐藏父目录。

## 范围与来源

提取自 [agent-privacy-guard](https://github.com/loonslo/agent-privacy-guard) 与
[x-follow-anlysis](https://github.com/loonslo/x-follow-anlysis)，配置能力另提取自
[ai-config](https://github.com/loonslo/ai-config)，来源版本和迁移边界见
各模块的迁移说明：[隐私审核](skills/privacy-review/docs/migration.md)、
[X 工具](tools/x-follow/docs/migration.md)、[配置同步](tools/ai-config-sync/docs/migration.md)。保留核心规则、范围约束、只读 API 和关系计算；
不包含网页、桌面应用、浏览器采集、SQLite、多账号、邮件、服务器、MCP、hook、语义模型或打包发布系统。
旧仓库历史和运行数据不复制。

X 客户端衍生代码保留原 MIT 声明，见 [LICENSE](tools/x-follow/LICENSE)。该声明来自 X 项目，
不代表为未声明许可的原隐私项目追授许可；本次为同一所有者的本地整合。

## 开发验证

```powershell
py -3.14 -m pip install -e "tools/x-follow[dev]"
py -3.14 -m pip install -e "tools/ai-config-sync[dev]"
```

测试从对应模块目录执行 `py -3.14 -B -m pytest`，各自维护 `pytest.ini` 和测试导入配置。
两个工具各自维护 `pyproject.toml` 和安装命令，不再安装整个仓库或提供跨工具统一命令。
独立验证说明见 [X 工具](tools/x-follow/README.md#开发验证)、
[配置同步](tools/ai-config-sync/README.md#开发验证)、[隐私审核](skills/privacy-review/docs/testing.md)。

隐私集成测试需要本机 Gitleaks；缺失会跳过并明确显示，不代表密钥扫描通过。
测试仅使用合成内容、临时 Git 仓库和模拟接口，不调用付费 API。
