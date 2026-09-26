# Zhilume Worker

Python + asyncio 的主动连接执行端。当前 0.4.0，提供 CPU 视频截取/抽音轨、显式模拟能力，以及可选择启用的 Qwen Image 2512 / 2.1 ComfyUI 执行器。配套 Studio 0.7.0 / Server 0.5.0。本地假 ComfyUI 联调已通过；未执行真实 GPU 推理。

新增 [云端部署准备](deploy/README.md)：独立 Python 环境、明确模型清单、软链接规划/应用、只读服务检查和默认不启用图片执行的启动脚本。模型链接在本机 WSL Ubuntu 实测，不等于云平台或 GPU 验收。

## 本地运行

需要 Python 3.11+ 和 uv。已在 Windows 的 Python 3.12.13 环境验证。

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

Qwen Image 2512 文生图与 2.1 文生图/指令编辑/多参考/RGBA 已有独立 API 工作流构建器。示例中的 4 张参考图、1536 像素上限是保守执行配置，不是已验收的 GPU 上限；不能用预检或测试服务代替推理验收。

优云智算目标区域：上海二 A（cn-sh2-01）、华北二 A（cn-wlcb-01）。开发包可以部署到已有实例；创建/启动 GPU 与真实生成前先告知用户。本轮没有申请或启动云实例。


## 可选 Qwen 执行器（真实 GPU 尚未验收）

默认不连接 ComfyUI、不发布图片执行能力。启用前，先告知用户将开始 GPU 试运行，并确认实例已准备好。

1. 为此 Worker 准备专用 ComfyUI，不与其他界面或 Worker 共用其执行队列。
2. 根据实际 `/object_info` 中的文件名修改 `config/comfy.example.json`。模型可从实例真实挂载目录软链接到 ComfyUI 模型目录；本程序不下载模型，也不把 `/models` 的目录声明当作挂载证据。
3. 只保留实例中已经具备的模型配置。缺少任一配置的文件/节点时启动失败，不伪装为可用。
4. 显式启动：

```bash
uv run zhilume-worker --server https://your-server --comfy-config config/comfy.local.json --enable-image-execution
```

`exclusive: true` 是部署约束。ComfyUI 必须支持客户端指定 UUID `prompt_id` 和带 `prompt_id` 的定向中断，参考 2026-09-26 官方接口；旧服务应先升级。上传参考图保留顺序，第一张确定编辑输出比例。工作流和加载器文件名由 Worker 管理，Studio 不传任意图或文件路径。

执行配置指纹包含模型文件名、workflow revision 和限制；多台 Worker 只有完全相同配置才接同一类任务。指纹不包含权重内容哈希，不是模型文件真实性证明。模型启用仅表示可试运行，公开 validation 仍为 unverified。

- 进度显示真实阶段，采样百分比未知时为不定进度；不模拟 GPU 进度。
- `/prompt` 响应丢失不自动重发，防止重复计费。取消只删除/中断本任务 UUID；停止状态无法确定时禁用图片接单，须检查 ComfyUI 后重启 Worker。
- 输出 PNG 最多 64 MB / 2000 万像素。RGBA 必须来自模型真实 RGBA 输出；普通 PNG 在白底合成为 RGB。原素材不被改写。
- prompt UUID 对应 Server attempt，提交前写入 `attempts/<id>/comfy-attempt.json`；重启先核对并停止遗留任务。若旧实例历史/队列均丢失而无法证明停止，启动失败，须人工核对该记录；不自动清空队列或重新生成。
- ComfyUI 输入/输出文件暂由实例运维清理，Worker 不调用不存在的文件删除接口。长时间断网、GPU OOM、驱动崩溃、实例重启与大图性能仍待 GPU 阶段验收。

工作流参考官方节点和示例格式，未复制旧 zhihua-service：
- https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_qwen.py
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_Image_2512.json
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_image_edit.json
- https://github.com/Comfy-Org/ComfyUI/blob/master/server.py
