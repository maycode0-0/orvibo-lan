# 架构说明

本文描述 Orvibo LAN 当前的运行时边界、数据流、并发模型和资源所有权。

```text
Home Assistant ConfigEntry
  |
  v
OrviboLanCoordinator ---- CloudClient ---- HTTPS ---- Orvibo 云端
  |                              |
  |                              `-- 家庭、房间、设备、状态、网关地址
  v
GatewayManager ---- GatewayConnection ---- TCP 8088 ---- MixPad
  |                    |-- 单一 Reader 循环
  |                    |-- serial/uniSerial -> Future
  |                    `-- cmd=42 推送分发
  v
StateStore ---- 不可变快照与 generation ---- HA 实体平台
```

云端负责引导拓扑和属性型只读状态。MixPad 局域网连接负责 Zigbee 子设备控制与实时状态。首次加载必须先取得云端拓扑，因此当前不能在没有任何有效拓扑缓存的情况下完全离线启动。

## 模块职责

| 模块                                | 职责                                                |
| ----------------------------------- | --------------------------------------------------- |
| `__init__.py`                       | ConfigEntry 生命周期、平台加载、服务注册和区域分配  |
| `config_flow.py`、`selection.py`    | 登录、家庭选择、设备选择、唯一 ID 和配置更新        |
| `lib/cloud_client.py`               | 云端认证、家庭读取、`readtable`、超时与错误分类     |
| `lib/protocol.py`、`models.py`      | 严格包模型、长度/CRC/加密校验和不可变值             |
| `lib/discovery.py`                  | UDP 网关候选发现、地址过滤和 UID 校验               |
| `lib/gateway_connection.py`         | TCP 会话、单 Reader、心跳、请求路由、推送分发和关闭 |
| `gateway_manager.py`                | 按网关 UID 管理连接、建连去重、地址更新和清理       |
| `state_store.py`                    | 保存带来源和 generation 的快照，拒绝陈旧更新        |
| `profiles.py`、`device_profiles.py` | 设备平台、能力和状态属性的集中判定                  |
| `lib/device_control.py`             | 生成不同协议族的设备控制 payload                    |
| `coordinator.py`                    | 编排云端、网关、状态仓库和 Home Assistant 更新      |
| 各实体平台                          | 将设备 Profile 和状态映射为 Home Assistant 实体     |

`lib/packet.py` 提供共享协议常量及封包接口；`lib/https_client.py` 和 `lib/lan_controller.py` 是项目内的适配接口。新的运行时逻辑应进入分层模块，不应绕过 `GatewayManager` 创建第二条读取链路。

## 生命周期

1. 配置流程认证账号，选择家庭和设备，创建版本为 3 的 ConfigEntry。
2. 协调器通过 `CloudClient` 获取设备、状态、房间、网关和局域网地址。
3. `GatewayManager` 按 UID 建立或复用 `GatewayConnection`。
4. 网关连接完成 Hello 与 Login，启动唯一 Reader 和心跳任务。
5. 实体平台根据集中式 Profile 创建实体并订阅状态快照。
6. 配置项卸载时先停止协调器任务和监听，再关闭全部连接，最后卸载平台。

Options Flow 修改设备选择后会触发配置项重载。配置更新和再认证过程保持同一账号/家庭边界，避免不同家庭共享唯一 ID。

## 并发与状态一致性

每个 `GatewayConnection` 只有一个读取循环。请求在写入 socket 前注册待响应 Future，Reader 按 `serial` 或 `uniSerial` 完成对应 Future；`cmd=42` 进入推送回调。缺少可靠关联字段的请求采用单飞控制，避免响应误配。

`StateStore` 为更新记录来源和 generation。LAN 推送优先于较旧的云端快照；云端刷新会对账设备和网关集合，移除失效状态，并关闭已删除或地址变化的连接。连续 LAN 推送在短暂防抖窗口内合并后只发布一次更新。

## 可用性

- 可控 LAN 实体：取决于所属 MixPad 的活动连接。
- 属性型只读实体：取决于云端协调器最近一次刷新结果。
- 云端暂时失败：不会自动把仍可局域网控制的设备标记为不可用。
- 网关地址变化：通过云端快照或 UDP 候选触发完整连接代次替换。

## 信任边界

- 云端 JSON、UDP 广播和 TCP 帧都按不可信输入处理。
- UDP 候选必须来自私有 IPv4 地址，并匹配云端已知网关 UID。
- TCP 包必须通过魔数、长度、类型、Session、CRC、AES 和 JSON 对象校验。
- 日志不得包含账号、密码、Token、Session Key 或完整控制 payload。
- TCP 8088 只应暴露在受信任局域网内。

## 发布边界

发布版本唯一来自 `custom_components/orvibo_lan/manifest.json`。标签格式为 `vX.Y.Z`，必须与 manifest 中的 `X.Y.Z` 完全一致。

HACS 使用 `zip_release=true`，固定资产名为 `orvibo_lan.zip`。ZIP 根目录直接包含 `manifest.json`、集成入口、平台、翻译和资源，不包含 `custom_components/orvibo_lan/` 目录前缀。
