# Zhilume Worker

Python + asyncio + FastAPI/Uvicorn 执行服务。当前 0.11.0，配套 Studio 0.13.0 / Server 0.10.0，协议 3.0。核心不默认安装推理环境。部署以 [通用 Linux / WSL2 部署](deploy/portable.md) 为准：程序安装/升级/回退、复用外部服务、托管 ComfyUI 进程及共享 GPU 资源隔离。管理页面内置于 wheel，不需要 Node.js。上海二 A 已通过复用现有环境的托管 Qwen/H3/IndexTTS 切换、取消停服、恢复及重启验收；全新 GPU 环境安装、其他平台和容器 GPU 仍待实测。

## 启动与接入

需要 Python 3.11+ 和 uv；部署脚本使用独立 Python 3.12。

```bash
uv sync --frozen
uv run zhilume-worker --state .state --show-token
uv run zhilume-worker --host 127.0.0.1 --port 4320 --state .state --name my-worker
```

第一条 Worker 命令只在本机显示接入密钥并退出，正常服务日志不输出密钥。在 Server 管理台填写从 Server 所在机器可访问的 Worker 地址和该密钥，然后测试并保存。Worker 不需要 Server 地址，所有网络请求均由 Server 发起；ComfyUI 仅在 Worker 内部访问。

默认仅监听本机；如通过受保护的网络入口访问，可指定适当的监听地址。公网访问应使用 HTTPS/WSS。地址的可达性由用户解决，本项目不实现 SSH 隧道、组网、中继，不提供外部工具配置示例或下载入口。

`--state` 保存 workerId、接入密钥、Server 绑定和任务尝试目录。首次连接绑定一个 Server 身份；其他 Server 被拒绝。切换 Server 时在部署管理页显式解除绑定或轮换接入凭证；任务执行期间禁止该操作。Server 也须保留数据目录内的 worker-connections.json，确保重启身份不变。不要把任何身份、密钥或用户素材放入镜像或 Git。

## 执行与恢复

- 默认单任务执行；每个 attempt 使用隔离目录；默认不开启 GPU。
- Server 上传输入，Worker 按声明大小/SHA-256 校验后原子落盘；已完整上传的文件可复用，未完成传输从头重试。
- Server 下载结果，校验并持久归档后发 commit_ack，Worker 才清理成功结果。
- 心跳 10 秒、默认租约 90 秒；Server 负责退避重连，Worker 租约过期停止执行。断网不自动重提生成任务。
- Worker 进程重启不自动重跑旧任务，由 Server 标记中断后手动重试。失败/取消临时文件暂时保留诊断，尚无自动保留策略清理。
- 输入输出接口仅允许当前有效 attempt/lease，无法通过任务请求读取任意路径。

## 校验与打包

```bash
uv run --extra image python -m unittest discover -s tests -v
# 仅开发/发布机器需要 Node.js
npm --prefix admin ci
npm --prefix admin run build
uv build
```

wheel 与源码包在 dist/，包括协议快照；契约由 Server/contracts 导出。Linux 安装和模型链接见 [部署说明](deploy/README.md)。本地模拟 ComfyUI 不代表真实 GPU 验收。

## 云端准备

视频截取和抽音轨已移至 Studio Electron / Server 共享模块，Worker 不再发布这两项能力。云端 FFmpeg 可供生成工作流使用，但不是接入和图片推理的先决依赖。

```bash
uv run zhilume-preflight --comfy-url http://127.0.0.1:8188 --model-root /model --model-root /models
```

只读预检仅访问 `/object_info` 并扫描指定的模型挂载目录，不提交 prompt，不下载/加载模型。报告中的 `environmentComplete` 只代表必要文件/节点存在，`inferenceVerified` 和 `executable` 始终为 false。

Qwen Image 2512 文生图与 2.1 文生图/指令编辑/多参考/RGBA 使用独立 API 工作流构建器。已验收 2512 的 1024×1024、50 步，2.1 的 1536×1536、25 步，以及 1024 参考分辨率下 4 张参考图。其他输入和尺寸组合仍需实测，不能把一个样本当作显存上限。

优云智算优先区域：上海二 A（cn-sh2-01），已实际部署；华北二 A（cn-wlcb-01）仍为后续目标。模型通过公共 `/model` 目录精确软链接，未下载或复制权重。


## 可选 Qwen 执行器

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

本机 WSL CPU/协议验收和上海二 A 云端 Worker 15/15 自动测试均已通过。云端网络、公共模型挂载及列出的真实 GPU 样本已验证；其他区域、镜像冷启动、长时故障恢复尚未覆盖，见 [部署说明](deploy/README.md)。


## 可选 IndexTTS 2.5 执行器（上海二 A 已实测）

Worker 的轻量依赖不安装 PyTorch。另建官方 IndexTTS Python 环境，固定提交 `ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c`，按其锁文件安装推理依赖。将 `config/indextts.example.json` 复制为本地配置，填写 Python、源码目录、模型目录、FFmpeg 的绝对路径。不要将模型路径或命令交给 Studio 指定。

```bash
uv run zhilume-worker --speech-config config/indextts.local.json --enable-speech-execution
```

部署启动器也支持 `ZHILUME_ENABLE_SPEECH=1` 和 `ZHILUME_SPEECH_CONFIG`。启用不会自动下载模型；主模型、tiktoken 词表、w2v-bert、BigVGAN、CAMPPlus 均须完整存在。需要文字情绪时额外准备 qwen0.6bemo4-merge，并设置 enableEmotionText=true。预检仅核对固定源码版本、配置与权重文件存在，不能证明 Python/CUDA 依赖、文件内容正确或推理成功。2026-09-27 已在上海二 A 核验这些公共模型，以 24 个准确文件软链接完成真实推理，未下载模型权重；其他区域仍须核对。文字情绪必须包含 chat_template.jinja 和分词文件，缺失或空文件时预检失败。完整安装步骤与清单见 [语音部署](deploy/speech.md)。

每个任务启动独立受管 Python 进程：FFmpeg 截取/规范化参考，再调用官方 infer_v2_5.IndexTTS2，最后输出 24kHz 单声道 PCM16 WAV。语速映射 duration_factor=1/speed；跟随音色、情绪音频、情绪向量、文字情绪互斥。启动加载成本按真实阶段显示，不伪造百分比。结果归档后清理成功任务；失败日志保留于 attempts/<attemptId>/speech.log。取消和 15 分钟推理超时结束所持有子进程；Linux 另设父进程退出保护。生产 GPU 部署优先 Linux；Windows 仅完成普通取消测试，异常强杀整棵进程树仍需独立验收。

共享 GPU 同时启用 ComfyUI 与 IndexTTS 时，Worker 单并发不等于显存已释放；ComfyUI 缓存可能占用显存。首次验收只启用语音执行器，随后再验证混合模型切换，不假设显存自动调度。普通视频截取和抽音轨仍不依赖本执行器。

`tests/speech_worker.py` 与服务端 speech-integration 测试是 CPU 测试夹具：替代上游推理、保留真实通信/文件传输/FFmpeg/受管 runner，不能用于发布或推理质量验收。正式 wheel 不包含 tests。上海二 A 已通过中文跟随音色、0.75/1/1.5 倍语速、独立情绪参考、情绪向量、文字情绪、真实取消及取消后新任务。结果经本机 Server 无入站监听上传/拉取与校验归档。参考素材使用官方演示样本；这不代表主观音色相似度、全部语言、长文本、OOM 或混合 ComfyUI/IndexTTS 显存切换已验收。

官方依据：[固定版推理接口](https://github.com/index-tts/index-tts/blob/ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c/indextts/infer_v2_5.py)、[词表加载](https://github.com/index-tts/index-tts/blob/ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c/indextts/utils/tokenizer.py)。


## H3 视频

通过 `--enable-video-execution --video-config config/video.local.json` 显式启用，详见 [视频部署](deploy/video.md)。FL2VA 文本/首尾帧，Ref2VA 图片/视频/音频参考；与图片共用受管 ComfyUI 生命周期，与语音共用子进程取消。上海二 A 512×288、124 帧样本已完成真实推理与取消后恢复；不是全配置性能或质量认证。

标准推理环境安装见 [安装说明](deploy/standard-install.md)。0.11 增加候选依赖锁、安装前检查、阶段状态及取消；标准安装的新独立环境已通过上海二 A Qwen / IndexTTS / H3 FL2VA GPU 样本验收，详见安装说明中的范围与网络限制。

## 可选通用语言模型

新增独立 llama.cpp 执行器，支持复用程序和单文件 GGUF；核心无推理依赖。配置、检查、接单、真实推理验收分开。当前完成协议与生命周期测试，尚未完成此适配器的真实 GPU 验收。[部署说明](deploy/language.md)。
