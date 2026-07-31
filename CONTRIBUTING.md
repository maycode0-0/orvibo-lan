# 贡献指南

感谢你改进 Orvibo LAN 。提交前请确认变更不会把账号、密码、Token、Cookie、Session Key、真实设备 ID、家庭信息或完整控制 payload 写入代码、日志、测试夹具和公开报告。

## 开发环境

- Python 3.11 或更高版本。
- Git。
- 不要求克隆 Home Assistant Core 仓库。

```bash
python -m venv .venv
python -m pip install --upgrade "pip>=25.1,<26"
python -m pip install --group dev
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活。项目不提交依赖 lock 文件，开发工具位于 `pyproject.toml` 的 `dependency-groups.dev`。

## 本地检查

```bash
ruff check custom_components tests
mypy custom_components/orvibo_lan
pytest --cov=custom_components.orvibo_lan --cov-report=term-missing
python -m unittest discover -s tests -p "test_package_contract.py" -v
python -m unittest discover -s tests -p "test_release_workflow.py" -v
```

CI 还会运行 HACS、hassfest 和 actionlint 校验。包契约测试会实际构建 `orvibo_lan.zip` 并检查 ZIP 根目录。

## 修改原则

- 网络协议、云端解析和状态更新应先有脱敏复现样本，再补实现和测试。
- 网关 TCP 读取只能由 `GatewayConnection` 的单一 Reader 循环负责。
- 新设备识别优先扩展 `profiles.py` 和 `device_profiles.py`。
- 不得在没有协议证据时仅按商品名或宽泛 `deviceType` 宣称支持。
- 异步任务、监听器、TCP Writer 和 aiohttp Session 必须有明确关闭路径。
- 不得禁用 TLS 校验或在异常链中泄露 URL 查询参数和敏感值。
- 发布版本唯一来自 `custom_components/orvibo_lan/manifest.json`。

提交说明应包含用户可见行为、协议证据、测试范围和仍需实机验证的部分。新增设备请使用 [设备扩展资料模板](DEVICE_EXTENSION_GUIDE.md)。
