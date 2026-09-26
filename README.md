# Zhilume Worker

Python + asyncio 的主动连接执行端。当前 0.2.0，提供 **视频截取、抽取音轨（CPU）** 和文本回显/原文件复制两个显式模拟能力。没有执行 GPU 推理。

## 本地运行

需要 Python 3.10+ 和 uv。已在 Windows 的 Python 3.12.13 环境验证。

```powershell
uv sync --frozen
uv run zhilume-worker --server http://127.0.0.1:4310 --enrollment <一次性接入凭证> --name my-worker
```

接入凭证由 Server 管理台生成，10 分钟内有效且只能使用一次。也可通过 `ZHILUME_ENROLLMENT` 环境变量传入，避免留在命令历史中。随后启动不再需要接入凭证：

```powershell
uv run zhilume-worker --server http://127.0.0.1:4310 --name my-worker
```

`--state` 默认 `.state`，保存独立 Worker 凭证和任务尝试目录。不同 Worker 必须用不同状态目录。换 Server 也应使用新的状态目录。不要将 `.state`、凭证或临时素材提交到 Git。

`--delay 3` 设置模拟计算秒数，便于测试取消。CPU 媒体进度来自 FFmpeg 输出时间；模拟进度仅用于协议测试。

## 运行约定

- 只向 Server 发起 HTTP / WebSocket 连接，没有 FastAPI 监听端口。
- 默认一次执行一个任务；按 attempt 隔离输入与输出目录。
- 输入下载流式校验 SHA-256 和大小，输出流式上传。
- 等待 Server 持久归档确认后才清理成功结果。
- 心跳每 10 秒；租约到期停止本地任务；网络重连指数退避并带抖动。
- 同进程断线可继续等待确认；进程重启不擅自重新执行旧任务，由 Server 标记中断后人工重试。
- 暂存失败或取消文件暂时保留用于诊断，尚未实现按保留策略自动清理。

## 校验与打包

```powershell
uv run python -m unittest discover -s tests
uv build
```

输出 wheel 和源码包在 `dist/`，契约快照包含在包内。协议源由 Server 仓库管理，当前 schema 主要校验消息信封，业务输入和状态另由 Server 校验。

远程主机将地址换为可访问的 HTTPS Server，不需要为 Worker 开放公网入站端口。当前仅有本机端到端证据，**尚未验证优云智算、AutoDL、Linux 或真实 GPU 推理**。

## CPU 处理与云端准备

先安装支持 libx264 / AAC / PCM 的 FFmpeg，加入 PATH，或设置 `ZHILUME_FFMPEG` 为可执行路径。Worker 在找不到 FFmpeg 时不会发布 CPU 媒体能力。Linux 可使用系统包管理器安装 FFmpeg；无需 GPU、CUDA、ComfyUI，也无需新增 FastAPI 服务。

媒体任务按范围精确解码/编码，输出 MP4（H.264/AAC）或 WAV（PCM）。FFmpeg 为受管理子进程，任务取消或租约过期时停止；素材仍通过 Server 上传归档。

```bash
uv run zhilume-preflight --comfy-url http://127.0.0.1:8188 --model-root /model --model-root /models
```

只读预检仅访问 `/object_info` 并扫描指定的模型挂载目录，不提交 prompt，不下载/加载模型。报告中的 `environmentComplete` 只代表必要文件/节点存在，`inferenceVerified` 和 `executable` 始终为 false。

Qwen Image 2512 只规划文生图；2.1 规划文生图、指令编辑、多参考和 RGBA。按实际 ComfyUI 节点与模型挂载选择 workflow 后，GPU 阶段再固定参考上限、尺寸、workflow revision 与真实执行适配器；不能用预检代替推理验收。

优云智算目标区域：上海二 A（cn-sh2-01）、华北二 A（cn-wlcb-01）。开发包可以部署到已有实例；创建/启动 GPU 与真实生成前先告知用户。本轮没有申请或启动云实例。
