# AI Config Sync

将个人规则和明确选定的共享设置部署到 Codex／Claude，并提供小范围配置备份、恢复和撤销。
从原 ai-config 提取事务、备份和回滚底座，结束桌面产品与完整换机平台的定位。

Python 3.14+。规则、JSON 设置和备份操作使用标准库；更新选定的 Codex TOML 字段时，
复用原项目依赖 `tomlkit==0.13.3`，以保留目标文件里的注释和其他配置：

```powershell
py -3.14 -m pip install -r ai-config-sync/requirements.txt
```

## 配置来源与本机目标

- `common/`：共享规则正本，修改规则先改这里。
- `codex/config.toml`、`claude/settings.shared.json`：共享字段模板，只有本机明确选中的字段会应用。
- `device.example.json`：本机目标模板；复制为被 Git 忽略的 `device.json`，按实际情况修改。
- `ai_config_sync/`：规划、选择性 ZIP 和本地事务引擎；不依赖原仓库。

`device.json` 的助手根目录必须已经存在；没有安装的助手应删除对应字段，不自动创建助手目录。
所有本机路径使用绝对路径或 `~/`。`state_dir` 不能位于助手根目录内，也不能与其重叠。
本机状态包含原文件备份和操作记录，不要提交或分享。

`codex_keys`、`claude_keys` 默认空列表：默认只部署规则，不改设置。
需要共享设置时再明确选择，例如：

```json
{
  "state_dir": "~/.bankai-tools/ai-config-sync",
  "codex": "~/.codex",
  "claude": "~/.claude",
  "codex_keys": ["model_reasoning_effort"],
  "claude_keys": ["autoMemoryEnabled"]
}
```

Codex 可选字段：`approval_policy`、`sandbox_mode`、`web_search`、`model_reasoning_effort`、
`project_doc_max_bytes`；Claude 可选 `autoMemoryEnabled`。这沿用原项目字段范围，
并非本次对各客户端最新版格式的实机验证。权限与安全字段需自行决定是否共享。
密钥、供应商、MCP、环境变量、登录状态和未知字段不进入共享集合；目标中的未选中字段保持原值。

## 运行

以下从仓库根目录运行；也可以使用 `run.py` 的绝对路径从任意目录启动。
每条会写入的命令默认只预览，显式加 `--apply` 才执行。

```powershell
# 先复制模板并填写本机路径
Copy-Item ai-config-sync/device.example.json ai-config-sync/device.json

# 预览，然后部署规则和选定设置
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json sync
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json sync --apply

# 查看本机操作记录，用返回的 ID 预览／执行撤销
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json history
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json undo <operation-id>
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json undo <operation-id> --apply

# 选择性导出；默认预览，加 --apply 才生成 ZIP
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json backup --output data/config-backup.zip
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json backup --output data/config-backup.zip --apply

# 在另一设备填写其 device.json，再预览／恢复
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json restore data/config-backup.zip
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json restore data/config-backup.zip --apply

# 发现中断事务时先检查，再显式回滚
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json recover
py -3.14 -B ai-config-sync/run.py --device ai-config-sync/device.json recover --apply
```

安装本仓库后也可使用统一命令。安装包只包含代码，规则源仍由源码目录维护，安装模式应显式传入配置源：

```powershell
py -3.14 -m pip install -e ".[config-sync]"
bankai ai-config-sync --device "<本机device.json绝对路径>" sync --source "<仓库绝对路径>/ai-config-sync"
```

退出码：`0` 表示预览或操作成功，`2` 表示冲突、条件不满足或明确拒绝，`1` 表示其他失败。
预览返回 0 不代表已经写入；以 `status` 为准。

## 冲突、备份和撤销

规则沿用 `<!-- ai-config:begin -->` / `<!-- ai-config:end -->` 标记，只更新受管块，保留块外内容。
首次遇到已有不同受管块，或受管块／共享字段被本机改动，默认拒绝覆盖；源规则正常更新则可以继续部署。
检查并决定舍弃所选范围内的本机差异后，可在 `sync` 或 `restore` 添加 `--overwrite-conflicts`；
仍需 `--apply` 才写入。不自动将本机修改反向合并到共享源。

写入前校验规划时的输入哈希，备份完整原文件并记录事务，失败时回滚。
撤销前校验备份哈希和当前文件；后续用户修改不会被强行覆盖。并发操作使用本地锁。
状态目录属于本机可信数据，不能导入第三方提供的操作记录；便携迁移只通过本工具的新 ZIP 格式。

便携 ZIP 只含选中助手的规则文件全文、选中共享字段的 JSON 和哈希清单，不含原机器绝对路径。
规则正文若本身写有路径或个人信息，仍会随正文保留；导出前会拒绝规则命中的疑似凭据，
这不是完整隐私保证。分享前可另用 privacy-review Skill 审查。
ZIP 不覆盖已有文件，恢复时只映射到当前 `device.json` 的已声明目标；已有不同内容默认冲突。
设置恢复只处理当前设备仍明确选中的字段，其余保留。ZIP 不加密，原文件备份可能含敏感设置，应只在本机保管。

## 明确边界

只支持这里定义的配置部署与选择性备份，不提供桌面应用、服务器、账号、自动网络同步、
记忆／聊天迁移、项目接续、设备回执、插件／Skill 安装、进程检测或完整换机迁移。
操作前请关闭相关助手。规则与选定配置由本仓库版本控制；其他设备自行获取该源码后预览、应用，
工具不执行 Git fetch、commit 或 push。

不接受旧 `.aiconfig`、`ai-machine-bundle` ZIP、旧 `device.json` 或旧状态目录；
使用新的独立状态目录，保留旧仓库、旧备份和原有部署作为历史恢复路径。
文件落盘正确不代表助手已读取；需要在新会话中检查规则版本和实际行为。
本次测试为 Windows 临时目录和合成内容，不代表真实跨机、macOS 或客户端加载已经验收。
