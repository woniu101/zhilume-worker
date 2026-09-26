# Zhilume Worker

Python + asyncio + FastAPI/Uvicorn 执行服务。当前 0.5.0，配套 Studio 0.8.0 / Server 0.6.0，协议 2.0。提供 CPU 视频截取/抽音轨、模拟执行和显式启用的 Qwen Image 2512 / 2.1 ComfyUI 执行器；真实 GPU 推理尚未验收。

## 启动与接入

需要 Python 3.11+ 和 uv；部署脚本使用独立 Python 3.12。

```bash
uv sync --frozen
uv run zhilume-worker --state .state --show-token
uv run zhilume-worker --host 127.0.0.1 --port 4320 --state .state --name my-worker
```

第一条 Worker 命令只在本机显示接入密钥并退出，正常服务日志不输出密钥。在 Server 管理台填写从 Server 所在机器可访问的 Worker 地址和该密钥，然后测试并保存。Worker 不需要 Server 地址，所有网络请求均由 Server 发起；ComfyUI 仅在 Worker 内部访问。

默认仅监听本机；如通过受保护的网络入口访问，可指定适当的监听地址。公网访问应使用 HTTPS/WSS。地址的可达性由用户解决，本项目不实现 SSH 隧道、组网、中继，不提供外部工具配置示例或下载入口。

`--state` 保存 workerId、接入密钥、Server 绑定和任务尝试目录。首次连接绑定一个 Server 身份；其他 Server 被拒绝。连接不同 Server 应停下当前 Worker，并使用独立的新状态目录启动，不复制已有身份文件。Server 也须保留数据目录内的 worker-connections.json，确保重启身份不变。不要把任何身份、密钥或用户素材放入镜像或 Git。

## 执行与恢复

- 默认单任务执行；每个 attempt 使用隔离目录；默认不开启 GPU。
- Server 上传输入，Worker 按声明大小/SHA-256 校验后原子落盘；已完整上传的文件可复用，未完成传输从头重试。
- Server 下载结果，校验并持久归档后发 commit_ack，Worker 才清理成功结果。
- 心跳 10 秒、默认租约 90 秒；Server 负责退避重连，Worker 租约过期停止执行。断网不自动重提生成任务。
- Worker 进程重启不自动重跑旧任务，由 Server 标记中断后手动重试。失败/取消临时文件暂时保留诊断，尚无自动保留策略清理。
- 输入输出接口仅允许当前有效 attempt/lease，无法通过任务请求读取任意路径。

## 校验与打包

```bash
uv run python -m unittest discover -s tests -v
uv build
```

wheel 与源码包在 dist/，包括协议快照；契约由 Server/contracts 导出。Linux 安装和模型链接见 [部署说明](deploy/README.md)。本地模拟 ComfyUI 不代表真实 GPU 验收。

## CPU 处理与云端准备

先安装支持 libx264 / AAC / PCM 的 FFmpeg，加入 PATH，或设置 `ZHILUME_FFMPEG` 为可执行路径。Worker 在找不到 FFmpeg 时不会发布 CPU 媒体能力。Linux 可使用系统包管理器安装 FFmpeg；无需 GPU、CUDA、ComfyUI，接入服务使用 FastAPI/Uvicorn。

媒体任务按范围精确解码/编码，输出 MP4（H.264/AAC）或 WAV（PCM）。FFmpeg 为受管理子进程，任务取消或租约过期时停止；输入由 Server 上传，输出由 Server 下载并归档。

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
uv run zhilume-worker --comfy-config config/comfy.local.json --enable-image-execution
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

本机 WSL Ubuntu 22.04 / Python 3.12 完整 CPU 与协议验收已通过，包含真实部署脚本注册及身份复用；云端网络、公共模型挂载与真实 GPU 推理仍待验收，见 [部署说明](deploy/README.md)。
