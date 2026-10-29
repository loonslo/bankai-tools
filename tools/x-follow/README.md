# X 关系工具

Python 3.14+，仅用标准库。调用 twitterapi.io 获取关注和粉丝列表，计算互关、未回关和仅关注我的账号。

在仓库根目录运行；也可从任意位置通过 `run.py` 的绝对路径运行，无需安装 Python 包。
密钥通过进程环境变量 `TWITTERAPI_IO_KEY` 提供，可以从本机安全存储注入，不通过命令参数或配置文件保存。

```powershell
# 读取 API（供应商按请求计费）；每类最多 20 页，每页 200 人
py -3.14 -B tools/x-follow/run.py fetch example_user --max-pages 20 --output data/snapshot.json

# 用完整快照离线重算，无网络请求
py -3.14 -B tools/x-follow/run.py analyze data/snapshot.json --output data/relations.json
py -3.14 -B tools/x-follow/run.py analyze data/snapshot.json --format csv --output data/relations.csv
```

输出路径相对于当前工作目录。推荐存放到仓库内被 Git 忽略的 `data/`。
也可在仓库根目录 `py -3.14 -m pip install -e .`，之后使用 `bankai x-follow ...` 或
`py -3.14 -B -m bankai_tools x-follow ...`。

分页按账号 ID 去重，默认每类上限 10000 人，请求间隔 5.2 秒，可用 `--max-items`、
`--max-pages`、`--interval` 调整。无自动重试、无后台任务；页数上限限制请求数量，不保证金额预算。
请求失败、重复／缺失游标或达到上限时，保存已采集数据并返回退出码 2，停止后续采集。
只有两类列表均收到 API 明确结束信号，才输出关系分析；否则需重新采集到新文件。
启动阶段失败返回 1，成功返回 0。程序不覆盖已有导出文件；CSV 对公式前缀进行转义。

完整表示 API 分页完成，不表示与平台真实数据绝对一致；两个列表顺序采集，期间关系可能变化。
快照和导出含个人信息，请自行控制分享范围。程序不发帖、点赞、私信、关注或取关。
