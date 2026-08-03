# 安全策略

安全修复面向当前受支持版本。请通过当前项目仓库提供的私密安全报告渠道联系维护者，不要在公开 Issue、日志或抓包中提交账号、密码、Token、Cookie、Authorization 头、TCP Session Key、真实家庭/设备/网关 ID、公网 IP 或门锁凭据。

报告应包含受影响版本、复现条件、预期影响和最小脱敏样本。同一设备或网关在不同样本中的替换值必须一致，否则无法验证路由关系。

该集成通过 Orvibo 云端获取设备拓扑，并在局域网连接 MixPad TCP 8088。不要把 8088 暴露到公网，也不要关闭 HTTPS 证书校验。开放 Home Assistant 8123 不等于开放 MixPad 8088，但必须检查路由器端口转发、DMZ、UPnP/NAT-PMP 和 IPv6 入站规则。

推荐将 MixPad 放入 IoT VLAN，只允许 Home Assistant 主机访问 MixPad TCP 8088。需要自动发现时，再单独允许必要的 UDP 10000 通信。普通终端、访客 Wi-Fi、其他 IoT 设备和 WAN 不应访问 8088。

MixPad 局域网协议由厂商定义，使用固定初始密钥、AES-ECB 和 CRC32，没有 TLS 设备证书或加密消息认证。客户端会尽早校验已知网关 UID、限制状态推送来源并脱敏日志，但无法单方面替换固件协议。UID 字符串比较不等价于加密身份认证，网络隔离仍是必要措施。

当前集成不提供门锁解锁控制。未经代码和实机验证的固件命令不属于项目能力或安全承诺。完整修复进度见 [SECURITY_REMEDIATION_CHECKLIST.md](SECURITY_REMEDIATION_CHECKLIST.md)。

CI 使用的第三方 Action 固定到已核验的 commit SHA，发布写权限只授予发布任务。

维护者会确认报告、评估影响并协调修复与披露时间。在修复发布前，请避免公开可直接利用的细节。
