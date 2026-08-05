# Orvibo LAN 协议参考

本文记录 Orvibo LAN 当前实现所使用的云端拓扑接口、局域网发现、TCP 会话、设备命令和状态格式。内容以仓库源码和脱敏实测样本为边界；未在代码中实现的控制通道不属于本项目能力。

## 通信边界

| 通道      | 目标        | 用途                                    |
| --------- | ----------- | --------------------------------------- |
| HTTPS     | Orvibo 云端 | 登录、家庭列表、设备/状态/网关/房间拓扑 |
| UDP 10000 | 局域网广播  | 为云端已知网关发现候选私有 IPv4 地址    |
| TCP 8088  | MixPad 网关 | 会话认证、心跳、设备控制和状态推送      |

云端当前只用于认证、拓扑和状态快照。本项目没有实现独立的云端设备控制长连接。

## 二进制包格式

局域网包由 42 字节头和 AES 加密的 JSON 对象组成：

```text
offset  size  field
0       2     magic: ASCII "hd"
2       2     total length, unsigned big-endian
4       2     packet type: ASCII "pk" or "dk"
6       4     CRC32 of encrypted payload, big-endian
10      32    session ID
42      n     AES-ECB + PKCS7 encrypted UTF-8 JSON
```

实现约束：

- 最小完整包必须包含至少一个 16 字节 AES 密文块。
- 总长度最大为 `0xffff`。
- `pk` 使用默认密钥，主要用于会话初始化。
- `dk` 必须使用已知 Session ID 对应的会话密钥。
- 解密结果必须是 UTF-8 JSON 对象，数组或标量会被拒绝。
- 魔数、长度、包类型、CRC、Session、AES 和 JSON 任一校验失败都会产生协议错误。

默认密钥为：

```text
khggd54865SNJHGF
```

它只用于协议规定的初始化包，不应作为通用设备凭据或写入诊断输出。

## 命令号

当前网关会话使用以下 `cmd`：

| cmd | 用途                                  |
| --: | ------------------------------------- |
|   0 | Hello，获取 Session ID 和 Session Key |
|   2 | Login，认证网关会话                   |
|  15 | Zigbee 子设备控制                     |
|  32 | 心跳，默认每 60 秒发送                |
|  42 | 设备状态推送                          |
|  86 | UDP 网关发现                          |

`cmd=98` 的电动晾衣架 payload 构造器仍存在于协议库，但集成没有注册相应控制服务或实体后端，因此不属于当前可用功能。

## 网关发现

UDP 发现向 `255.255.255.255:10000` 发送 `pk` 包：

```json
{
  "cmd": 86,
  "serial": 123456,
  "uniSerial": 123456,
  "clientType": 1,
  "serverRecord": false,
  "ver": "5.1.3.309"
}
```

发现响应只被视为候选地址。项目还会验证：

- 来源是私有、非回环、非组播的 IPv4 地址。
- 数据包通过完整协议校验。
- 响应 `uid` 属于云端拓扑中的已知网关。
- 候选地址上的 TCP 会话能登录并确认预期 UID。

UDP 不负责发现 Zigbee 子设备。

## TCP 会话

```text
TCP connect MixPad:8088
  -> Hello (cmd=0, pk, default key)
  <- Session ID + Session Key
  -> Login (cmd=2, dk, session key)
  <- status=0 and expected gateway UID
  -> Heartbeat (cmd=32, every 60 s)
  -> Control (cmd=15)
  <- correlated response
  <- Status Push (cmd=42)
```

每条连接只有一个 Reader。请求写入前先注册待响应 Future，响应优先按 `serial` 或 `uniSerial` 关联；缺少可靠关联字段的请求采用单飞方式。`cmd=42` 不参与请求完成，直接交给状态回调。

无效帧、CRC 错误、连接关闭或写入失败会关闭当前连接代次并使待处理请求失败，避免旧连接的迟到数据污染新连接。

## 控制 payload 公共字段

`device_control.py` 构造的 LAN 控制通常包含：

```json
{
  "cmd": 15,
  "serial": 123456,
  "uniSerial": 123456,
  "deviceId": "DEVICE_A",
  "uid": "GATEWAY_A",
  "groupId": "",
  "order": "on",
  "value1": 0,
  "value2": 0,
  "value3": 0,
  "value4": 0,
  "qualityOfService": 1,
  "defaultResponse": 1,
  "propertyResponse": 0,
  "source": "ZhiJia365"
}
```

不同设备可能省略或增加字段。`groupId`、四个 `value`、响应策略和 `source` 不能在没有实机证据时随意删除。

## 灯光协议

### 数值字段协议

类型 0、1、38 和 102 使用 `order` 及 `value1-value3` 的组合。当前命令包括：

| 操作 | order                    | 主要字段                              |
| ---- | ------------------------ | ------------------------------------- |
| 开   | `on`                     | `value1=0`，部分类型同时携带亮度/色温 |
| 关   | `off`                    | `value1=1`                            |
| 亮度 | `move to level` 或 `on`  | 百分比或设备量程值，按 Profile 选择   |
| 色温 | `fast color temperature` | Kelvin 值                             |

类型 1/102 且 `subDeviceType=1` 的已知设备按仅开关 Profile 处理。

### ThingModel 协议

类型 501、502 和 503 使用 `order="set property"`：

```json
{
  "cmd": 15,
  "order": "set property",
  "deviceId": "DEVICE_A",
  "uid": "GATEWAY_A",
  "properties": {
    "onoff": { "status": "on" },
    "brightness": { "percent": 80 },
    "colorTemp": { "value": 4000 }
  }
}
```

| type | 当前能力         |
| ---: | ---------------- |
|  501 | 开关             |
|  502 | 开关、亮度       |
|  503 | 开关、亮度、色温 |

实际能力仍可由子类型和 Model Profile 收窄。

## 窗帘协议

类型 34 和 35 使用以下动作：

| 动作 | order                      | value1 |
| ---- | -------------------------- | -----: |
| 打开 | `open` 或设备适配的 `on`   |    100 |
| 关闭 | `close` 或设备适配的 `off` |      0 |
| 停止 | `stop`                     |      0 |
| 位置 | 由当前 Profile 映射        |  0-100 |

不同电机可能反转 0/100 方向。没有完整位置样本时，不应假设同类型设备方向一致。

## 空调协议

类型 36 和 81 的控制会保留当前模式、风速和温度字段：

| 操作 | order                 | value1 |   value2 |   value3 |       value4 |
| ---- | --------------------- | -----: | -------: | -------: | -----------: |
| 关机 | `off`                 |      1 | 当前模式 | 当前风速 | 当前温度编码 |
| 开机 | `on`                  |      0 |   模式码 |   风速码 | 目标温度编码 |
| 模式 | `mode setting`        |      0 |   模式码 | 当前风速 | 当前温度编码 |
| 温度 | `temperature setting` |      0 | 当前模式 | 当前风速 | 目标温度编码 |
| 风速 | `wind setting`        |      0 | 当前模式 |   风速码 | 当前温度编码 |

已知模式码：`2=除湿`、`3=制冷`、`4=制热`、`7=送风`。温度以摄氏度乘 100 后放入 `value4` 高 16 位。具体设备仍应以实测 payload 为准。

## Fan 协议

类型 81 和 516 通过 `fan_on`、`fan_off` 与 `fan_set_speed` 构造器映射开关和速度。类型 516 的实际模式、档位和状态字段在不同新风型号上可能不同，目前应视为需要实机验证的协议路径。

## 状态推送

### 数值字段状态

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

`value1-value4` 的含义由设备类型决定。状态合并只更新推送中出现的字段。

### ThingModel 状态

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

`properties` 按属性级合并。项目还识别 `battery_power`、`door_status`，以及门锁开门方向和用户字段，支持常见的 snake_case/camelCase 名称与标量/嵌套值。开门方向统一为 `inside`/`outside`，用户名称或编号会经过长度和控制字符校验；Home Assistant 分别显示为“最近开门方向”和“最近开门用户”。连续推送会经过短暂防抖后通知 Home Assistant。

## 类型与平台索引

|                                    deviceType | 平台          | 验证说明                       |
| --------------------------------------------: | ------------- | ------------------------------ |
|                                 0、1、38、102 | Light         | 数值字段协议；能力按子类型收窄 |
|                                 501、502、503 | Light         | ThingModel 属性协议            |
|                                        34、35 | Cover         | 35 及部分位置方向需要实机确认  |
|                                        36、81 | Climate       | 命令需携带当前状态字段         |
|                                       81、516 | Fan           | 516 需要更多型号样本           |
| 22、23、25、26、27、46、54、56、107、300、522 | Sensor        | 只读取已知且合法的数值属性     |
|                        25、26、27、46、54、56 | Binary Sensor | 告警与开合类状态               |

以下类型即使能出现在云端拓扑中，也不表示本项目可在 LAN 中控制：WiFi 直连设备、摄像头、红外设备、BLE 设备、门锁和未知传输设备。

## Model ID 使用原则

[MODEL_ID_POOL.md](MODEL_ID_POOL.md) 是研究索引，不是支持列表。Model ID 只能辅助缩小设备范围，不能单独证明状态语义或控制协议。新增 Profile 必须同时有设备元数据、控制请求、回复、状态推送和实物结果。

## 安全要求

- 不记录或提交账号、密码、Token、Cookie、Session Key 和门锁凭据。
- 同一设备和网关的脱敏 ID 必须跨样本保持一致。
- 不要把 MixPad TCP 8088 暴露到公网。
- 不要关闭 HTTPS 证书校验。
- 原始 UDP/TCP 数据始终按不可信输入处理。
