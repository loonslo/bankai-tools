# 隐私审核独立测试

在 privacy-review 目录运行，Python 3.14+，需要 pytest；集成测试另需 Git 和 Gitleaks 8.x。

```powershell
py -3.14 -B -m pytest --basetemp=<仓库外每次全新的临时目录>
```

测试、pytest.ini 和导入配置均随本 Skill 保存；不依赖其他工具源码或根目录测试配置。
缺少 Gitleaks 时跳过对应集成测试，不代表扫描能力已验收。
所有输入使用合成材料与隔离 Git 仓库，不读取真实凭据，不修改真实目标。

本机全局 pytest 插件如产生无关行为，可在当前进程设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 后运行。
