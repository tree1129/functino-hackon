# G1 交付目录

机器上的目录：

```text
/home/galbot/galbot_delivery
```

## 内容

```text
galbot_delivery/
├── galbot_code_package.zip          # 云端控制台源码 + 全身动画工程 + DEPLOY.md
├── galbot_bell_shake_package.zip    # 摇铃/右腕动作包
├── galbot_animation.zip             # 全身动画工程（含视觉跟踪）
├── galbot_vision_tracking_package.zip # 视觉人脸跟踪基础包
├── galbot_vision_tracking_selfcontained.zip # 自包含视觉跟踪包（推荐）
├── scripts_ops/                     # 本次全部操作/调试脚本
├── SHA256SUMS
└── README_DELIVERY.md
```

## 从另一台电脑下载

公网（frp）：

```bash
scp -P 20022 -r galbot@107.175.67.78:/home/galbot/galbot_delivery ./galbot_delivery
```

局域网：

```bash
scp -r galbot@192.168.43.51:/home/galbot/galbot_delivery ./galbot_delivery
```

## 云端控制台

```text
http://107.175.67.78:28080/frontend/core.html
```

本次启动访问码：

```text
K3QC7Z8Q
```

控制台重启后访问码会变，需从启动日志重新获取。

## 说明

- `scripts_ops/` 里的敏感字符串已替换为 `<REDACTED_SUDO>` / `<CONSOLE_CODE>`。
- `galbot_animation/assets/g1` 是部分 URDF/网格；完整资产在机器人
  `/home/galbot/galbot-sdk-web-console/assets/robots/g1`。
- 云端控制台源码在 `galbot_code_package.zip` 里的 `galbot-sdk-web-console/`。
- 全身动画回放、摇铃动作、camera preset、core console、motion 服务都包含在代码包中。
- 视觉跟踪独立包：`galbot_vision_tracking_package.zip`，解压后看 `VISION_TRACKING.md`。
- 自包含视觉跟踪包（推荐）：`galbot_vision_tracking_selfcontained.zip`，内含脚本、YuNet 模型、
  启停/安装脚本、systemd 服务、实测样本和 `SHA256SUMS`。解压后进入
  `galbot_vision_tracking_selfcontained/`，看 `README.md`。
  真机运行：
  `/home/galbot/galbot_animation/run_face_tracking.sh --execute --duration 30`
  该功能只控制头部两个关节，不控制手臂、夹爪、腿或底盘。
