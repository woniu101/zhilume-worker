# H3 视频执行

使用 ComfyUI 固定提交 `79be670e2d9be63e238785af307369d2b9039ed1` 的原生 MiniMax H3 节点。Worker 0.8.0，目录 1.6.0，协议仍为 2.0。模型存在和工作流校验不等于推理已验收。

## 模型与启动

`video-models.shanghai.example.json` 的五个文件路径来自 2026-09-27 上海二 A 真实挂载。仅创建软链接，不复制或下载权重。华北二 A 的模型目录可见性不代表该区实例验收。

```bash
uv run --frozen zhilume-prepare --manifest deploy/video-models.shanghai.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models
# 检查明确的源文件后创建软链接，已有文件不覆盖
uv run --frozen zhilume-prepare --manifest deploy/video-models.shanghai.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models --apply-links
cp config/video.example.json config/video.local.json
export ZHILUME_ENABLE_VIDEO=1
export ZHILUME_VIDEO_CONFIG=config/video.local.json
bash deploy/run.sh
```

先通过 `run-comfy.sh` 启动专用 ComfyUI 并等到就绪，再启动 Worker。配置中 FFmpeg、FFprobe 必须是已存在的绝对路径；它们用于 H3 参考归一化与输出检查，不承接普通视频截取/抽音轨任务。可与图片执行器复用一个专用 ComfyUI，Worker 保持单并发；无法确认停止时，同端点的图片和视频能力一并下线。

FL2VA 使用 pruned int8 convrot 权重，Ref2VA 使用对应权重；共享 Qwen3VL 32B NVFP4 AWQ、视频 FP16 VAE 与音频 FP32 VAE。示例为标准 20 步，不隐式启用 Turbo LoRA。配置 profileId 绑定工作流、文件名及开放参数，不是权重内容哈希。

## 输入输出边界

- FL2VA：文生视频、首帧、尾帧、首尾帧；首尾帧角色显式传递。当前上游首帧拉伸、尾帧按输出比例裁切，应选择匹配的输出比例。
- Ref2VA：图片、视频、音频参考重生成。按各类型顺序编号 `<Picture 1>` / `<Video 1>` / `<Audio 1>`。视频参考初版只提供画面，声音单独添加音频参考；不默认从视频提取音轨。
- 模型上限图片 9 / 视频 3 / 音频 3，总数 12；部署示例仅开放 2 / 1 / 1。视频、音频参考总长度分别不超过 15 秒。片段至少 56 帧、按 17 帧递增，且不超过输出帧数；这是当前适配器限制。
- 输出帧数为 17k+5，初版范围 124–345、24 FPS。示例只开放 124 帧，即约 5.17 秒；不能用整数 5 秒替代实际时长。尺寸按配置明确开放，都是 32 像素倍数。
- 视频参考先以 FFmpeg 规范到 24 FPS 和指定帧数，声音规范为 32kHz 立体声。验证轨道和实际时长后才提交 GPU；同一源文件可用于多个不同角色/片段。
- 输出为 H.264 MP4，可保留 32kHz 立体声音轨或静音导出。H3 仍联合生成音画，不宣称静音节省模型推理。输出尺寸、帧数、编码和音轨检查通过才归档。
- 与图片共用 ComfyUI 提交、指定 prompt ID 取消、重启对账；参考 FFmpeg 子进程与语音共用取消/超时管理。输出超过 256 MiB 明确失败。

可通过 Server 的 `scripts/accept-video-cloud.mjs <mode> --execute` 重跑真实验收，必须显式提供实例地址与凭证环境变量；脚本不启动实例、不输出凭证。`text / first-last / reference / cancel / after-cancel` 覆盖基本功能；真实结果以 Server 验收报告为准。提示词优化服务尚未实现，提示词原样提交。
