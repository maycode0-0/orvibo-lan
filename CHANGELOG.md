# Changelog

本文件记录 Orvibo LAN 作为独立项目的用户可见变化。

## [Unreleased]

## [0.0.5] - 2026-08-01

### Fixed

- 兼容登录响应 UID 为空但云端已确认网关地址的 MixPad 固件，同时继续拒绝未确认的 UDP 候选地址。

## [0.0.4] - 2026-07-31

### Fixed

- 使用 MixPad Hello 响应中的网关 UID 完成身份校验，兼容登录成功响应不重复返回 UID 的固件。

## [0.0.3] - 2026-07-31

### Fixed

- 配置选项页兼容 Home Assistant 的只读 ConfigEntry 属性，不再返回 500。
- 启动后的区域分配通过线程安全任务调度执行，并避免重复移除一次性监听器。
- 云端只读设备的可用性由协调器刷新结果决定，不再被设备的离线字段误判。

### Changed

- 云端设备和网关快照会移除失效记录并关闭对应连接。
- 控制请求使用进程级原子序列号，避免并发命令关联冲突。
- LAN 状态通知在防抖窗口结束后统一发布。
- 云 API 拒绝请求时会重新认证一次，再向 Home Assistant 报告失败。
- ConfigEntry 使用版本 3，唯一 ID 按账号和家庭隔离。
- 实体可用性按 LAN 网关和云端数据源分别判断。
- 提供 `orvibo_lan.refresh_devices` 服务手动刷新拓扑。
- Ruff、mypy 和 pytest coverage 覆盖整个集成，覆盖率门槛为 62%。

### Security

- 所有 HTTPS 请求启用默认 TLS 校验。
- 网络异常和调试日志不记录凭据、会话密钥或完整控制 payload。

## [0.0.2] - 2026-07-31

### Changed

- 自动发布会等待完整 Validate 与发布包契约检查全部通过。

### Fixed

- Wi-Fi 设备不再引用不存在的 MixPad 网关，避免 Home Assistant 设备注册警告。

## [0.0.1] - 2026-07-31

Orvibo LAN 独立项目的首个发布版本。

### Added

- Home Assistant UI 配置、家庭选择、设备选择和再认证流程。
- Orvibo 云端拓扑读取与 MixPad 局域网控制。
- 多网关管理、UDP 地址发现、单 Reader TCP 请求路由和状态推送。
- Light、Cover、Climate、Fan、Sensor 和 Binary Sensor 平台。
- 集中式设备 Profile、来源感知状态仓库和资源关闭路径。
- HACS ZIP 发布、自动化质量检查和项目维护文档。
