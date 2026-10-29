# 合并边界

2026-10-29 从以下本地干净源码提取，原仓库保留，未复制其 Git 历史、认证、环境或运行数据。

| 来源 | 源提交 | 保留 | 移除 |
| --- | --- | --- | --- |
| loonslo/agent-privacy-guard | 35fc8974d5eea1e4007f42f02ba28cc11f513b4a | Skill、本地规则、Gitleaks 调用、只读 Git 范围、来源提示、哈希定位 | MCP、语义模型、旧 hook、安装与发布脚本、独立 EXE |
| loonslo/x-follow-anlysis | 53693b925776e4b1e22cfb8fa2a8152a27af2052 | local 版 twitterapi.io 客户端、ID 集合关系计算、JSON／CSV 思路 | Web、GUI、浏览器采集、数据库、白名单、断点恢复、服务器多账号及部署 |

隐私引擎随 Skill 一起携带，只导出建议接口；保留读取前路径检查、结果脱敏和不完整状态，
删去旧执行决策与语义模型接口。扫描器通过 PATH 或环境变量复用，不带外部二进制。
X 采集与分析改为小型标准库命令行；分页数据只保存在一次运行的快照中，失败不推导未回关。
API 客户端拒绝重定向，避免将密钥随重定向转发。

这是功能提取，不是旧部署的数据迁移或功能平替；旧服务状态未检查。
初次提取未修改、删除、归档或推送两个来源仓库。整合仓库现为 [loonslo/bankai-tools](https://github.com/loonslo/bankai-tools)。
Skill 统一放在 `skills/`，小工具统一放在 `tools/`；X 工具位于 `tools/x-follow/`。
后续工具仍以独立模块或 Skill 加入，
除非有明确需求，不引入统一服务、插件系统或共享数据库。
