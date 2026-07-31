# 设备扩展指南

本文说明如何基于 Orvibo LAN 当前架构接入新设备，以及提交设备资料时必须包含哪些信息。

## 接入前提

当前控制链路是“云端获取拓扑，MixPad 局域网执行控制”。最适合扩展的是同时满足以下条件的设备：

1. 设备能在云端 `readtable.device[]` 中出现。
2. 设备通过 MixPad 接入，`uid` 能映射到已识别网关。
3. App 操作时能观察到 TCP 8088 控制请求或 `cmd=42` 状态推送。
4. 能提供脱敏后的设备、初始状态、控制请求、网关回复和状态推送。

WiFi、BLE、红外、摄像头和第三方云设备通常需要新的通信后端，不能只增加一个 `deviceType`。

## 当前扩展模型

```text
CloudClient
  `-- device[] + deviceStatus[] + gateway[] + room[]
       |
       v
device_profiles.py / profiles.py
  |-- 平台与能力判定
  |-- 状态属性归一化
  `-- 未知设备筛选
       |
       v
HA 平台实体 -- device_control.py -- Coordinator
                                      |
                                      v
GatewayManager -- GatewayConnection -- MixPad
                    |-- 单 Reader
                    |-- 请求响应路由
                    `-- cmd=42 推送
```

### 主要扩展点

| 文件                               | 作用                               | 新设备常见改动                        |
| ---------------------------------- | ---------------------------------- | ------------------------------------- |
| `profiles.py`                      | `deviceType` 到 HA 平台的基础映射  | 声明设备可能创建的平台                |
| `device_profiles.py`               | 类型、子类型、Model 和状态属性能力 | 增加精确能力或协议族判定              |
| `selection.py`、`config_flow.py`   | 候选设备过滤和选择                 | 验证网关归属及未知设备行为            |
| `lib/device_control.py`            | 控制 payload                       | 新增或复用命令构造器                  |
| `coordinator.py`、`state_store.py` | 快照合并、来源优先级和状态发布     | 增加新的状态字段或转换                |
| 对应平台文件                       | 实体状态与服务调用                 | 创建正确实体并声明能力                |
| `translations/`                    | 用户可见文本                       | 新增配置或错误信息时更新              |
| `tests/`                           | 行为和协议契约                     | 增加 Profile、payload、状态和实体测试 |

不要在实体平台中直接创建额外 TCP Reader，也不要绕过 `GatewayManager` 持有网关连接。请求必须经过单 Reader 路由，异步任务和网络资源必须有明确关闭路径。

## 可行性判断

按以下顺序判断新设备：

1. **确认传输**：设备是否通过 MixPad Zigbee 接入。
2. **确认发现**：`readtable` 是否包含完整设备对象和状态对象。
3. **确认归属**：设备 `uid` 是否能对应 MixPad UID。
4. **确认控制链路**：App 操作是否产生 MixPad TCP 8088 请求。
5. **确认状态链路**：物理或 App 操作是否产生 `cmd=42`，或只能从云端快照取得状态。
6. **确认协议族**：是否与现有类型完全一致，还是需要按 `subDeviceType` 或 `model` 分流。
7. **确认 HA 语义**：一个物理设备应创建哪些实体、范围、单位和枚举。

仅凭商品名、设备照片或 Model ID 不能证明协议相同。至少应使用：

```text
deviceType + subDeviceType + model + statusType + 实际控制/状态样本
```

## 当前平台映射

| 平台            | deviceType                                    |
| --------------- | --------------------------------------------- |
| `light`         | 0、1、38、102、501、502、503                  |
| `cover`         | 34、35                                        |
| `climate`       | 36、81                                        |
| `fan`           | 81、516                                       |
| `sensor`        | 22、23、25、26、27、46、54、56、107、300、522 |
| `binary_sensor` | 25、26、27、46、54、56                        |

状态包含 `battery_power` 或 `door_status` 时，还可能动态增加只读 `sensor` 或 `binary_sensor`。平台映射只说明代码存在处理路径，不代表该类型的所有型号均已实机验证。

## 需要提供的资料

### 基本信息

- 商品名、硬件型号和设备固件版本。
- `deviceType`、`subDeviceType`、`statusType` 和 `model`。
- 接入方式：MixPad Zigbee、WiFi、BLE、红外或其他。
- MixPad 型号、固件版本和智家 365 App 版本。
- 期望创建的 Home Assistant 实体、范围、单位、步长和枚举。

### 云端对象

请提供同一次 `readtable` 返回中的：

- 该设备完整 `device` 对象。
- 该设备完整 `deviceStatus` 对象。
- 所属网关完整 `gateway` 对象。
- 对应房间对象（如区域映射相关）。

关键字段不要遗漏：

```text
deviceId, uid, deviceType, subDeviceType, statusType, model,
deviceName, roomId, endpoint, value1-value4, properties, online
```

### 控制与状态样本

每个动作应提供连续的一组证据：

```text
操作前状态
-> App 中执行的动作及输入值
-> 发往 MixPad 的完整解密请求 JSON
-> 网关回复 JSON
-> 随后的 cmd=42 JSON
-> App 最终状态和设备实际表现
```

不要删除 `order`、`groupId`、`qualityOfService`、`defaultResponse`、`propertyResponse`、`source`、`serial`、`uniSerial`、`endpoint`、`value1-value4` 或 `properties`。这些字段可能影响网关接受命令或响应关联。

### 建议样本矩阵

| 类别       | 最低样本                                     |
| ---------- | -------------------------------------------- |
| 开关/面板  | 每路开关、物理按键、多路同时变化、endpoint   |
| 灯         | 开关，亮度 1/50/100%，色温最小/中间/最大     |
| 窗帘       | 开、关、停、位置 0/25/50/75/100，确认方向    |
| 空调       | 开关、全部模式、全部风速、最低/中间/最高温度 |
| 风扇/新风  | 开关、每档速度、自动模式和百分比换算         |
| 传感器     | 正常、触发、恢复、低电量、离线和异常值       |
| 多实体设备 | 每个子功能单独操作，并说明实体拆分方式       |

## 资料模板

````markdown
# 新设备接入资料

## 基本信息

- 商品名：
- 硬件型号：
- 设备固件：
- deviceType：
- subDeviceType：
- statusType：
- model：
- 接入方式：MixPad Zigbee / WiFi / BLE / 红外 / 其他
- MixPad 型号及固件：
- 智家 365 App 版本：
- Home Assistant 版本：
- 当前集成版本：

## Home Assistant 目标

| 实体平台  | 功能             | 范围/枚举           | 必须/可选 |
| --------- | ---------------- | ------------------- | --------- |
| 例：light | 开关、亮度、色温 | 1-100%、2700-6500 K | 必须      |

## readtable 数据

### device

```json
{}
```

### deviceStatus

```json
{}
```

### gateway

```json
{}
```

## 控制与状态样本

### 动作一

- 操作前状态：
- App 操作：
- 设备实际表现：

请求：

```json
{}
```

网关回复：

```json
{}
```

状态推送：

```json
{}
```

## 字段含义

| 字段路径 | 样本值 | 含义 | 单位/倍率 | 置信度 |
| -------- | -----: | ---- | --------- | ------ |
| `value1` |      0 | 开启 | 无        | 已实测 |

## 特殊情况

- 断网是否可控：
- 物理操作是否产生 cmd=42：
- 是否多通道：
- 0/100 或 on/off 是否反向：
- 离线/故障表现：
````

## 实现要求

一次完整接入至少包含：

- 精确的 Profile 和平台判定，不误收同类型但不同协议的设备。
- 稳定的实体 `unique_id` 和正确的 `via_device` 网关关系。
- 每项控制动作与样本一致的 payload。
- 初始快照和增量 `cmd=42` 的状态解析。
- 未知值、边界值、离线、重连和无响应处理。
- Profile、payload、状态合并和实体行为测试。
- README 支持范围与协议参考更新。
- App 操作、Home Assistant 操作和物理操作的实机验证。

## 脱敏要求

不得提交账号密码、密码摘要、Access Token、Cookie、Authorization 头、TCP Session Key、门锁凭据、家庭成员信息、公网 IP 或 WiFi 密码。

同一个 ID 在全部样本中必须使用同一替换值，例如将设备统一替换为 `DEVICE_A`，将所属网关统一替换为 `GATEWAY_A`。如果设备 `uid` 等于网关 UID，两处也必须使用相同替换值。
