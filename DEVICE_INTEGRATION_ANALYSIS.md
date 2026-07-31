# 设备接入与实现分析

本文从当前源码出发，说明 Orvibo LAN 的设备数据来源、运行链路、能力模型和仍需改进的边界。协议字段详情见 [DEVICE_PROTOCOL_REFERENCE.md](DEVICE_PROTOCOL_REFERENCE.md)，新增设备资料要求见 [DEVICE_EXTENSION_GUIDE.md](DEVICE_EXTENSION_GUIDE.md)。

## 结论

该集成采用“云端拓扑 + 局域网控制”的混合架构：

```text
Orvibo 账号
  |-- HTTPS --> 家庭、房间、设备、状态、网关 IP
  v
Home Assistant
  |-- UDP 10000 --> 已知 MixPad 地址候选
  `-- TCP 8088 --> Hello / Login / Heartbeat / Control
                    `-- cmd=42 --> 实时状态
```

当前方案适合通过 MixPad 接入的 Zigbee 子设备。WiFi、BLE、红外、摄像头及其他不经过 MixPad 的设备，需要独立传输后端或云端控制实现。

## 配置与数据流

### 配置流程

1. 用户输入 Orvibo 账号和密码。
2. `CloudClient` 登录并读取家庭列表。
3. 单家庭自动选择，多家庭由用户选择。
4. 配置流程获取设备拓扑并过滤不可接入项。
5. 用户选择设备后创建 ConfigEntry。

ConfigEntry 版本为 3，使用账号与家庭组合生成唯一 ID。凭据和家庭信息存放在 `entry.data`，设备选择存放在 `entry.options`。Options Flow 更新后会重载配置项。

### 运行时刷新

协调器每分钟刷新一次云端快照，用于：

- 对账设备和网关集合。
- 更新网关局域网地址。
- 补充 `battery_power`、`door_status` 等属性型只读状态。
- 移除已失效设备状态并关闭已移除网关连接。

可控 Zigbee 设备的实时状态主要来自 LAN `cmd=42`。`StateStore` 使用来源与 generation 防止较旧云端快照覆盖较新的局域网推送。

### 网关连接

`GatewayManager` 按 UID 持有连接并去重并发建连。地址变化时会替换完整连接代次。每条 `GatewayConnection` 只有一个 Reader：

```text
TCP Reader
  |-- serial/uniSerial 匹配 --> 请求 Future
  |-- cmd=42 ----------------> 状态回调
  `-- 无效包/断连 -----------> 当前代次关闭与待处理请求失败
```

UDP 发现只接受私有 IPv4 地址，并要求响应 UID 已存在于云端网关集合。UDP 不发现 Zigbee 子设备，也不会绕过云端身份边界。

## 能力模型

`profiles.py` 提供 `deviceType` 到平台的基础映射；`device_profiles.py` 进一步使用子类型、Model 和状态属性判断能力。实体平台不再各自承担候选设备发现。

| 平台          | 基础类型                                      |
| ------------- | --------------------------------------------- |
| Light         | 0、1、38、102、501、502、503                  |
| Cover         | 34、35                                        |
| Climate       | 36、81                                        |
| Fan           | 81、516                                       |
| Sensor        | 22、23、25、26、27、46、54、56、107、300、522 |
| Binary Sensor | 25、26、27、46、54、56                        |

灯能力会进一步区分仅开关、亮度和色温；状态中出现 `battery_power` 或 `door_status` 时可动态增加只读实体。未知但确认属于 MixPad 的设备可以保留在设备注册表中，但不会在没有能力证据时自动获得控制实体。

## 状态协议

项目处理两类主要状态：

### 数值字段

```json
{
  "cmd": 42,
  "deviceId": "DEVICE_A",
  "statusType": 2,
  "value1": 0,
  "value2": 80,
  "value3": 250,
  "value4": 0
}
```

`value1` 到 `value4` 的语义由设备类型决定，不能跨类型直接复用。

### ThingModel 属性

```json
{
  "cmd": 42,
  "deviceId": "DEVICE_A",
  "statusType": 503,
  "properties": {
    "onoff": { "status": "on" },
    "brightness": { "percent": 80 },
    "colorTemp": { "value": 4000 }
  }
}
```

属性推送按字段合并，单个属性更新不会清空同一设备的其他已知属性。数值解析拒绝布尔值、NaN、Infinity 和超出合法范围的百分比。

## 当前实现的优势

- 单 Reader 解决控制回复与状态推送的并发读取冲突。
- 全局原子序列号降低多连接并发请求的关联碰撞。
- 云端错误分为认证、HTTP、JSON、Schema 和传输错误。
- 云 API 拒绝后只重新认证一次，避免无限重试。
- 连接、任务、监听器和外部 Session 均有明确所有者与关闭路径。
- LAN 与云端实体采用不同可用性来源。
- 测试覆盖协议、网关连接、配置流程、状态存储、设备 Profile、云端客户端和发布包契约。

## 当前限制与风险

### 首次启动依赖云端

项目没有持久化足以离线启动的完整拓扑和会话信息。云端不可达时，首次配置或没有有效运行时数据的加载无法完成。

### Profile 仍以类型为主

基础映射仍以 `deviceType` 为主，只有部分灯具使用 `subDeviceType` 和 `model` 精细分流。同类型不同协议的设备仍必须通过真实抓包补充 Profile。

### 部分协议缺少实机证据

代码存在 `deviceType=35` 卷帘、`deviceType=516` 新风等处理路径，但不同型号的方向、档位和状态字段仍需更多实机样本验证。文档不能替代设备验收。

### 云端协议是外部依赖

认证与 `readtable` 接口不是本项目控制的稳定契约。响应字段、认证策略或服务可用性变化可能影响配置和拓扑刷新。

### 凭据位于 ConfigEntry

集成运行需要账号凭据。日志和诊断已避免输出敏感值，但部署者仍应保护 Home Assistant 配置目录、备份和调试包。

## 扩展优先级

后续扩展建议按以下顺序执行：

1. 收集完整、脱敏且可重复的真实协议样本。
2. 使用 `deviceType + subDeviceType + model` 定义精确 Profile。
3. 增加 payload 和状态 fixture 测试。
4. 增加实体行为、边界值和不可用状态测试。
5. 在 App、Home Assistant 和物理操作三条路径上实机验证。
6. 最后更新支持列表，明确“已实现”和“已实测”的区别。

## 验证基线

项目变更应至少通过：

```bash
ruff check custom_components tests
mypy custom_components/orvibo_lan
pytest --cov=custom_components.orvibo_lan --cov-report=term-missing
python -m unittest discover -s tests -p "test_package_contract.py" -v
python -m unittest discover -s tests -p "test_release_workflow.py" -v
```
