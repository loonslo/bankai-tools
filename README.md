# bankai-tools

个人轻量工具箱。一个仓库收纳独立小工具与 Skill，Python 3.14+，无 Python 运行时依赖。

| 入口 | 功能 |
| --- | --- |
| [privacy-review](skills/privacy-review/SKILL.md) | 自包含的隐私审核 Skill，检查指定文件、记忆／聊天导出、Git 暂存区和拟推送历史，返回脱敏建议 |
| [X 关系工具](tools/x-follow/README.md) | 调用 twitterapi.io 分页获取关注／粉丝，计算互关、我关注但未回关、仅关注我的账号，支持 JSON／CSV |

## 目录分类

```text
skills/                 # 自包含 Skill
  privacy-review/
tools/                  # 独立小工具
  x-follow/
    run.py              # 无需安装的运行入口
    bankai_tools/       # 工具代码
    README.md
tests/                  # 自动测试
docs/                   # 共用说明
```

新增 Skill 放入 `skills/<skill-name>/`，新增小工具放入 `tools/<tool-name>/`。

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
也可安装本仓库后使用 `bankai x-follow ...`；Skill 单独复制，不包含在 Python 包里。

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
[x-follow-anlysis](https://github.com/loonslo/x-follow-anlysis)，来源版本和迁移边界见
[docs/migration.md](docs/migration.md)。保留核心规则、范围约束、只读 API 和关系计算；
不包含网页、浏览器采集、SQLite、多账号、邮件、服务器、MCP、hook、语义模型或打包发布系统。
旧仓库历史和运行数据不复制。

X 客户端衍生代码保留原 MIT 声明，见 [LICENSE](LICENSE)。该声明来自 X 项目，
不代表为未声明许可的原隐私项目追授许可；本次为同一所有者的本地整合。

## 开发验证

```powershell
py -3.14 -m pip install -e ".[dev]"
py -3.14 -B -m pytest
py -3.14 -m ruff check .
```

隐私集成测试需要本机 Gitleaks；缺失会跳过并明确显示，不代表密钥扫描通过。
测试仅使用合成内容、临时 Git 仓库和模拟接口，不调用付费 API。
