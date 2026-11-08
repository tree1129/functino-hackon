# G1 视觉人脸跟踪

## 功能

- 读取 G1 头部相机（默认 `HEAD_LEFT_CAMERA`，1280×992 JPEG）
- 使用 OpenCV YuNet 检测人脸
- 用 `FaceLock` 锁定同一张脸，避免在多人的展厅里不断跳到最大的脸
- 只控制 `head` 两个关节：
  - `head_joint1`：左右 yaw
  - `head_joint2`：上下 pitch
- 默认 dry-run；只有加 `--execute` 才会让头部运动
- 不控制手臂、夹爪、腿、底盘

## 文件

```text
galbot_animation/
├── face_track_head.py
├── head_home.py
├── run_face_tracking.sh
├── run_head_home.sh
├── models/
│   └── face_detection_yunet_2023mar.onnx
└── out/vision/
    ├── latest.jpg
    └── *.jsonl
```

模型 SHA256：

```text
8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
```

## 运行

```bash
chmod +x /home/galbot/galbot_animation/run_face_tracking.sh
chmod +x /home/galbot/galbot_animation/run_head_home.sh

# 只看检测，不动头
/home/galbot/galbot_animation/run_face_tracking.sh --duration 10

# 真正跟踪，30 秒后自动停止
/home/galbot/galbot_animation/run_face_tracking.sh --execute --duration 30

# 连续跟踪，Ctrl-C 停止
/home/galbot/galbot_animation/run_face_tracking.sh --execute --duration 0

# 头回中位
/home/galbot/galbot_animation/run_head_home.sh
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--camera` | `head_left` | `head_left` / `head_right` |
| `--target` | `largest` | 初始锁定最大的脸；可选 `center` |
| `--score` | `0.65` | YuNet 置信度阈值 |
| `--min-area-ratio` | `0.004` | 过滤远处小脸，避免追路人 |
| `--deadband` | `0.06` | 人脸在画面中心附近不动 |
| `--gain-yaw` | `0.32` | 左右比例增益 |
| `--gain-pitch` | `0.22` | 上下比例增益 |
| `--max-step-yaw` | `0.06` | 每帧最大左右步长 |
| `--max-step-pitch` | `0.035` | 每帧最大上下步长 |
| `--max-yaw` | `0.80` | 左右限位（关节硬件约 ±1.52） |
| `--max-pitch-low` | `-0.21` | 抬头限位（硬件约 -0.214） |
| `--max-pitch-high` | `0.45` | 低头限位（硬件约 +0.494） |
| `--lost-hold` | `1.0` | 丢失后先保持 1 秒 |
| `--recenter-after` | `5.0` | 丢失 5 秒后缓慢回中 |
| `--save-every` | `1.0` | 每隔多少秒保存一张标注图 |

## 方向标定

实测：

- `head_joint1` 正方向 = 相机向左转
- `head_joint2` 正方向 = 相机向下看

所以默认：

```text
--yaw-sign 1.0
--pitch-sign -1.0
```

如果换相机或机械版本后发现追踪方向相反，把对应 sign 改成相反值即可。

## 已验证

- 真机头部相机：1280×992
- YuNet 检测：用户脸部置信度约 0.90–0.93
- 检测循环：约 7.6 FPS
- 一次 6 秒执行测试：45/45 帧持续锁定同一张脸
- 头部从 `(0.000, 0.000)` 运动到 `(-0.302, -0.210)`
- 人脸从画面右侧 `cx=909` 被带到中心附近 `cx=650`
- 全程只发 `["head"]` 关节命令，手臂/夹爪/腿/底盘未动

## 人脸识别与自动标签

使用 OpenCV `FaceRecognizerSF` + SFace 模型：

```text
/home/galbot/galbot_animation/models/face_recognition_sface_2021dec.onnx
```

人脸库目录：

```text
/home/galbot/galbot_animation/faces/
├── embeddings.npy
├── labels.json
├── meta.json
└── images/<name>/
```

### 现场注册

```bash
/home/galbot/galbot_animation/run_enroll_face.sh \
  --name User --samples 12 --duration 20
```

注册时人站在机器人正前方，脚本只读相机，不动头部。

### 从照片注册

```bash
python /home/galbot/galbot_animation/enroll_from_images.py \
  --name User --image /path/a.jpg /path/b.jpg
```

### 识别图片并打标签

```bash
python /home/galbot/galbot_animation/recognize_image.py \
  --image /path/input.jpg \
  --output /path/labeled.jpg
```

### 实时跟踪标签

`face_track_head.py` 启动时会自动加载 `faces/`；识别结果写入 JSONL 的
`identity` / `similarity` 字段，并画在 `latest.jpg` 上。

默认余弦阈值 `0.40`；同一个人的参考值约 `0.9+`，不同人约 `0.1–0.2`。

## 注意

- YuNet 模型必须在 `/home/galbot/galbot_animation/models/face_detection_yunet_2023mar.onnx`
- 必须在 `/home/galbot/galbot-sdk-web-console` 目录下用其 `.venv` 运行
- 运行时需要：
  ```bash
  export LD_LIBRARY_PATH=/data/galbot/lib:$LD_LIBRARY_PATH
  export PYTHONPATH=/data/galbot/lib:$PYTHONPATH
  ```
- 人多时默认锁定最大的脸；如果锁定错人，可先 `run_head_home.sh`，再重新运行
- 当前是“人脸 + 头部”跟踪；还没有接入人体/躯干检测和底盘跟随
