# 数据回放生成脚本

以下命令应在仓库根目录执行。生成的 `.rrd` 文件属于本地部署或测试产物，不应加入 Git。

## 机器人回放模型

录制与播放功能默认使用轻量碰撞模型。该模型由 collision URDF 生成，并以不透明橙色实体显示：

```bash
uv run python scripts/generate_robot_model_recording.py --model g1 --representation collision
```

如需查看机器人的精细外观，可额外生成 visual 模型：

```bash
uv run python scripts/generate_robot_model_recording.py --model g1 --representation visual
```

默认输出分别为：

- `assets/robots/g1/rerun/model.rrd`
- `assets/robots/g1/rerun/model-visual.rrd`

目标文件已存在时，脚本会拒绝覆盖。确认需要重新生成时添加 `--force`；也可以使用 `--output` 指定其他输出位置。

## 测试 Demo

以下命令生成包含机器人关节运动、底盘位姿、地图、LiDAR、导航路径、障碍物和相机数据的确定性测试记录：

```bash
uv run python scripts/generate_demo_recording.py --output recordings/demo_recording.rrd
```

目标文件已存在时可添加 `--force` 重新生成。播放 Demo 前仍需先生成默认碰撞模型，历史回放会在服务端将共享模型和 Demo 遥测合并为单一 Rerun 数据流。
