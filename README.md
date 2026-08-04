# Orvibo LAN

Orvibo LAN 是一个 Home Assistant 自定义集成。它通过 Orvibo 云端获取家庭、房间、设备和网关拓扑，并通过局域网内 MixPad 的 TCP 8088 接口控制 Zigbee 子设备。属性型 WiFi 门锁会额外建立云端 TCP 10002 长连接，持续接收门状态推送。

> 本地控制仅适用于可通过 MixPad 网关访问的 Zigbee 子设备。WiFi、BLE、红外、摄像头和其他直连设备不在当前 LAN 控制范围内。

## 功能

- 通过 Home Assistant UI 配置 Orvibo 账号、家庭和设备。
- 支持多个家庭与多个 MixPad 网关，配置项按账号和家庭隔离。
- 通过 UDP 10000 发现已知网关的新局域网地址。
- 通过 TCP 8088 完成 Hello、Login、控制、心跳和状态接收。
- 使用单一 TCP 读取循环按序列号路由响应，避免控制回复与状态推送相互抢读。
- 接收 `cmd=42` 局域网状态推送，并用云端快照补充属性型只读设备。
- 使用云端 TCP 10002 的双向 TLS 长连接接收 WiFi 门锁 `cmd=42` 属性状态，并在 Home Assistant 中实时更新。
- 将 App 房间映射到 Home Assistant 区域。
- 提供 `orvibo_lan.refresh_devices` 服务手动刷新设备拓扑。

## 运行要求

- Home Assistant 2024.1.0 或更高版本。
- Home Assistant 能访问 Orvibo 云端 HTTPS 接口。
- Home Assistant 能访问 Orvibo 云端 TCP 10002，用于 WiFi 门锁实时状态推送。
- Home Assistant 与 MixPad 位于可互通的局域网，且 TCP 8088 可达。
- Orvibo 账号中至少存在一个可识别的家庭和 MixPad 网关。

集成首次加载依赖云端拓扑，当前不支持在从未成功获取拓扑的环境中完全离线启动。账号、密码、Token 和会话密钥均不应写入日志或诊断资料。

## 安装

### HACS

1. 在 HACS 中将当前项目仓库添加为“集成”类型的自定义仓库。
2. 搜索并安装 **Orvibo LAN**。
3. 重启 Home Assistant。
4. 进入“设置 -> 设备与服务 -> 添加集成”，搜索 **Orvibo LAN**。

发布资产固定为 `orvibo_lan.zip`，HACS 从 ZIP 根目录安装集成文件。

### 手动安装

将 `custom_components/orvibo_lan/` 复制到 Home Assistant 配置目录下的 `custom_components/orvibo_lan/`，然后重启 Home Assistant 并添加集成。

## 配置

| 参数 |   必填   | 说明                                           |
| ---- | :------: | ---------------------------------------------- |
| 账号 |    是    | Orvibo 账号，通常为手机号                      |
| 密码 |    是    | Orvibo 账号密码                                |
| 家庭 | 多家庭时 | 单家庭会自动选择，多家庭需要在配置流程中选择   |
| 设备 |    是    | 从当前家庭中选择需要接入 Home Assistant 的设备 |
| 独立 LAN 凭据 |    否    | 仅在 MixPad 本地授权账号与云端家庭账号不一致时使用 |

配置完成后，集成会获取设备拓扑、解析所属网关、建立局域网连接，并按设备能力创建实体。配置选项发生变化后，配置项会重新加载以更新实体集合。

家庭所有者发生转移时，云端账号和 MixPad 保存的 TCP 8088 授权账号可能暂时不一致。如果日志持续出现 `Gateway login rejected ... (status=12)`，可在集成的“配置”选项中启用“使用独立的 MixPad LAN 凭据”，填写 MixPad 当前认可的账号。该账号只用于局域网 Login 和控制 payload，云端拓扑与状态仍使用主配置中的账号。关闭此选项会删除 LAN 凭据覆盖并恢复使用云端账号。

## 支持范围

源码中的设备能力以 `custom_components/orvibo_lan/profiles.py` 和 `device_profiles.py` 为准。文档中的“支持”表示已有实体和协议实现，不等同于所有同类型型号都经过实机验证。

| Home Assistant 平台 | deviceType                                    | 能力                                         |
| ------------------- | --------------------------------------------- | -------------------------------------------- |
| `light`             | 0、1、38、102、501、502、503                  | 开关、亮度、色温，具体能力由类型和子类型决定 |
| `cover`             | 34、35                                        | 开、关、停止、位置                           |
| `climate`           | 36、81                                        | 电源、模式、目标温度、风速                   |
| `fan`               | 81、516                                       | 电源和档位/百分比映射；516 仍需更多实机样本  |
| `sensor`            | 22、23、25、26、27、46、54、56、107、300、522 | 温湿度、电量及设备暴露的数值属性             |
| `binary_sensor`     | 25、26、27、46、54、56                        | 告警、人体、烟雾、门磁、水浸、紧急状态       |
| 属性型只读实体      | 状态中包含 `battery_power` 或 `door_status`   | 电量或门状态                                 |

以下设备当前不提供本地控制：

- WiFi 直连电动晾衣架、智能遥控器和其他 WiFi 设备。
- 摄像头、门铃、BLE 门锁和红外设备。
- 不经过已识别 MixPad 网关的设备。
- 协议或能力尚未通过抓包确认的未知型号。

## 工作原理

```text
Home Assistant
  |-- HTTPS --> Orvibo 云端
  |              |-- 家庭、房间、设备、状态和网关 IP
  |              `-- TCP 10002 --> WiFi 门锁 cmd=42 状态推送
  |
  |-- UDP 10000 --> 已知 MixPad 地址发现
  |
  `-- TCP 8088 --> MixPad
                   |-- Hello / Login / Heartbeat
                   |-- cmd=15 控制
                   `-- cmd=42 状态推送 --> Home Assistant 实体
```

云端负责拓扑和属性型只读状态，TCP 10002 长连接负责 WiFi 门锁的实时属性事件，局域网连接负责 Zigbee 子设备控制与实时状态。LAN 实体的可用性取决于所属网关连接；属性型只读实体的可用性取决于云端协调器。短暂云端故障不会直接使仍可通过 LAN 控制的设备离线。

## 协议摘要

- 包格式：42 字节头 + AES-ECB/PKCS7 加密的 JSON。
- 包头：`hd`、总长度、`pk`/`dk`、CRC32、32 字节 Session ID。
- 默认密钥：`khggd54865SNJHGF`，仅用于初始会话包。
- 会话密钥：Hello 成功后由网关返回，用于后续 `dk` 包。
- 心跳：`cmd=32`，默认 60 秒。
- 控制：`cmd=15`。
- 状态推送：`cmd=42`。
- 云端门锁推送使用独立的 TCP 10002 双向 TLS 会话，客户端证书随集成发布，服务器证书使用固定指纹校验。

协议字段和设备命令详见 [DEVICE_PROTOCOL_REFERENCE.md](DEVICE_PROTOCOL_REFERENCE.md)。新增设备所需资料见 [DEVICE_EXTENSION_GUIDE.md](DEVICE_EXTENSION_GUIDE.md)。

## 安全与隐私

TCP 8088 是 MixPad 提供的局域网控制端口，不是 Home Assistant 的监听端口。开放 HA 8123 不会自动开放 8088，但路由器端口转发、DMZ、UPnP/NAT-PMP 或宽松的 IPv6 入站规则可能让 MixPad 暴露到公网。

推荐将 MixPad 放入独立 IoT VLAN，并使用最小访问规则：

```text
允许：Home Assistant IP -> MixPad IP TCP 8088
拒绝：其他 LAN/访客/IoT 客户端 -> MixPad IP TCP 8088
拒绝：WAN -> MixPad TCP 8088
```

需要自动发现时，再单独允许 Home Assistant 使用 UDP 10000；否则应阻断跨 VLAN 广播。Orvibo 账号应使用不与其他服务共用的强密码，Home Assistant `.storage`、备份、日志和抓包应作为机密资料保存。

厂商局域网协议使用固定初始密钥、AES-ECB 和 CRC32，不提供 TLS 设备证书或加密消息认证。项目会校验网关 UID、严格限制状态推送并脱敏日志，但这些措施不能替代网络隔离，也不能根治厂商协议的监听和重放风险。当前集成不提供门锁解锁控制，不对未实现的固件命令作安全承诺。详细事项见 [安全策略](SECURITY.md)。

## 开发

开发环境使用 Python 3.11 或更高版本：

```bash
python -m venv .venv
python -m pip install --upgrade "pip>=25.1,<26"
python -m pip install --group dev
ruff check custom_components tests
mypy custom_components/orvibo_lan
pytest --cov=custom_components.orvibo_lan --cov-report=term-missing
```

Windows PowerShell 激活虚拟环境时使用 `.venv\Scripts\Activate.ps1`。完整开发约束和发布流程分别见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [RELEASING.md](RELEASING.md)。

## 文档

- [架构说明](ARCHITECTURE.md)
- [设备接入分析](DEVICE_INTEGRATION_ANALYSIS.md)
- [设备扩展指南](DEVICE_EXTENSION_GUIDE.md)
- [协议参考](DEVICE_PROTOCOL_REFERENCE.md)
- [Model ID 资料池](MODEL_ID_POOL.md)
- [变更日志](CHANGELOG.md)
- [安全策略](SECURITY.md)

## 致谢

感谢 https://github.com/mozzie1121/orvibo-lan-control.git 提供的参考。

## License

MIT
