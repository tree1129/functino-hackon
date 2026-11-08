# Galbot SDK Web Console

用于通过 Galbot SDK 监控、可视化和操作 Galbot 机器人的 Web 控制台。

## 使用

需要安装 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --dev
uv run galbot-sdk-web-console --live
```

使用录制与播放功能前需要生成机器人回放模型；模型与测试 Demo 的生成方式见 [scripts/README.md](scripts/README.md)。
