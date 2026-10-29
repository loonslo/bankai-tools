# bankai-tools

个人轻量工具箱。一个仓库收纳独立小工具与 Skill，Python 3.14+，无 Python 运行时依赖。

| 入口 | 功能 |
| --- | --- |
| [privacy-review](skills/privacy-review/SKILL.md) | 自包含的隐私审核 Skill，检查指定文件、记忆／聊天导出、Git 暂存区和拟推送历史，返回脱敏建议 |
| `py -3.14 -B -m bankai_tools x-follow` | 调用 twitterapi.io 分页获取关注／粉丝，计算互关、我关注但未回关、仅关注我的账号，支持 JSON／CSV |

## X 关系工具

在仓库根目录运行。密钥通过进程环境变量 `TWITTERAPI_IO_KEY` 提供，不通过命令参数或配置文件保存。
可以从本机安全存储注入环境变量。本工具不会保存密钥。

```powershell
# 读取 API（供应商按请求计费）；每类最多 20 页，每页 200 人
py -3.14 -B -m bankai_tools x-follow fetch example_user --max-pages 20 --output data/snapshot.json

# 用完整快照离线重算，无网络请求
py -3.14 -B -m bankai_tools x-follow analyze data/snapshot.json --output data/relations.json
py -3.14 -B -m bankai_tools x-follow analyze data/snapshot.json --format csv --output data/relations.csv
```

也可 `py -3.14 -m pip install -e .` 后使用 `bankai x-follow ...`；Skill 仍从源码目录单独复制，
不包含在 Python 包里。

分页按账号 ID 去重，默认每类上限 10000 人，请求间隔 5.2 秒；可用 `--max-items`、
`--max-pages`、`--interval` 调整。无自动重试、无后台任务。调用本身只读，但第三方 API 会计费；
页数上限用于限制请求数量，不保证金额预算，也不承诺供应商价格。
请求失败、重复／缺失游标或达到上限时，保存已采集数据并返回退出码 2，停止后续采集。
只有两类列表均收到 API 明确结束信号，才输出关系分析；否则需重新采集到新文件。
启动阶段失败返回 1；成功返回 0。程序不覆盖已有导出文件，名单保存在被 Git 忽略的 `data/`。
CSV 对公式前缀进行转义。

完整表示 API 分页完成，不表示与平台真实数据绝对一致；两个列表顺序采集，期间关系可能变化。
快照和导出含个人信息，请自行控制分享范围。程序不发帖、点赞、私信、关注或取关。

## 隐私审核 Skill

将 `skills/privacy-review/` 整个目录放入支持 Skill 的客户端目录，或直接运行其中脚本：

```powershell
py -3.14 -B skills/privacy-review/scripts/review.py files --repo "<待检查项目目录>" --paths README.md
```

本机需要 Gitleaks 8.x（本次验收为 8.30.1），Git 模式另需 Git。可用 `PRIVACY_GUARD_GITLEAKS`
指定扫描器绝对路径，不捆绑程序、不自动下载。只返回建议；发现风险仍返回 0，检查不完整返回 1。
为避免误读认证目录，审核引擎拒绝隐藏根目录及隐藏父目录，当前 `.bankai-tools` 目录也在此限制内；
如需审核工具箱本身，请先将拟分享文件复制到普通命名的隔离目录。

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
