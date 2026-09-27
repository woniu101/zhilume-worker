# 优云智算部署准备

优先上海二 A，华北二 A 后续验证。2026-09-26 已创建上海二 A RTX 5090 实例，完成真实部署、推理、素材传输、取消及 Server 重连验收。固定环境见 [comfy-runtime.json](comfy-runtime.json)，完整样本报告见 [Server 云端验收记录](https://github.com/woniu101/zhilume-server/blob/main/docs/cloud-acceptance-2026-09-26.md)。部署到专用目录，不修改其他项目的 ComfyUI 或 Python 环境。

实测配置为 5090 32GB 显存、14 vCPU、48 GiB 内存、100 GiB 系统盘。64 GiB 规格虽然预检通过，实际创建时资源不足；48 GiB 在同一区域创建成功。库存与入口需要每次现场确认。

## ComfyUI 运行基线

基础镜像 `compshareImage-1vjzi0b9thpu` 中 ComfyUI 版本较旧，缺少 2.1 节点。须在专用目录升级至 `comfy-runtime.json` 的官方提交并安装该版本 requirements。国内 Python 源可能缺少新模板依赖，实际缺失时使用官方 PyPI 补齐；无需下载模型。Worker 依赖与 ComfyUI 的 Torch 环境分开。

```bash
export ZHILUME_COMFY_ROOT=/root/ComfyUI
export ZHILUME_COMFY_PYTHON=/root/miniconda3/bin/python
bash deploy/run-comfy.sh
```

脚本固定只监听本机、禁用自定义节点和 xFormers，采用 PyTorch attention。镜像默认 xFormers 的 mask/kernel 路径在 5090 上曾真实失败，不能只看 `/object_info` 就判定可推理。脚本只启动既有专用安装，不自动更新源码或下载权重。若镜像已有 ComfyUI 自启动，应先确认队列为空，再停止原进程并修改启动项，避免两个实例占用同一端口。

## 1. 安装独立 Worker

在已有实例克隆本仓库，安装 uv 后运行 `bash deploy/install.sh`。使用单独 Python 3.12 环境和 `uv.lock`，不会修改镜像自带的 Python 3.10，也不安装 Torch 或下载模型。云端 FFmpeg 可保留供生成工作流使用；普通视频截取和抽音轨由 Studio / Server 自带 FFmpeg 执行，不依赖此实例。

## 2. 链接公共权重

依据实际挂载检查 `deploy/models.example.json`。只列举 Qwen 2512、2.1 的明确文件路径，不递归链接整个公共库。示例来自目录快照，不保证当前实例具有这些文件；缺失时应修正清单或选择具有权重的区域，工具不会自动下载。

```bash
uv run --frozen zhilume-prepare --manifest deploy/models.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models
# 检查计划后显式创建链接，已有不同文件绝不覆盖：
uv run --frozen zhilume-prepare --manifest deploy/models.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models --apply-links
```

同名候选对应多个实际文件会报告歧义。需要针对实际目录修正清单，不能静默选择模型版本。软链接不会复制权重进镜像。

## 3. 启动 Worker 接入服务

```bash
export ZHILUME_HOST=127.0.0.1
export ZHILUME_PORT=4320
export ZHILUME_STATE=/root/zhilume-worker/.state
uv run --frozen zhilume-worker --state "$ZHILUME_STATE" --show-token
bash deploy/run.sh
```

把显示的密钥和从 Server 所在机器能访问的 Worker 地址填入 Server 管理台。Server 无需公网地址；不会向 Worker 提供任何回调地址。默认不开图片能力。监听地址按用户自行配置的网络入口选择；公网入口须保护为 HTTPS/WSS。密钥只保存在用户数据目录，不写进镜像或仓库。

## 4. 只读检查与 CPU 验收

`zhilume-prepare` 默认检查 Python/FFmpeg；提供 `--worker <地址>` 时从环境变量 ZHILUME_WORKER_TOKEN 读取密钥，只 GET /api/v1/system。提供 `--comfy-config config/comfy.local.json` 时只读检查内部 ComfyUI /object_info，不提交 prompt。

接入后在管理台核对心跳、能力、忙碌状态；执行文本回显、素材复制与已启用的图片生成，并检查结果归档。断开再连、取消、Worker/Server 重启分别验收。每个 Worker 状态目录只绑定一个 Server；Server 保留 worker-connections.json 以复用稳定身份。

网络可达性由用户解决，本项目不实现或引导配置 SSH 隧道、组网、中继。不同可用区的实际访问入口仍需现场验证。

## 5. GPU 验收范围与镜像

显式设置 `ZHILUME_ENABLE_IMAGE=1` 和 `ZHILUME_COMFY_CONFIG` 才发布图片执行。上海二 A 已完成 2512 文生图、2.1 文生图/编辑/2 与 4 图参考/RGBA、取消、Server 离线 12 秒后原 attempt 归档，以及 Worker 重启后的重新接入。GPU OOM、驱动崩溃、长时断网及其他区域仍待验收。公开 profile 的 `validation: unverified` 不会因本次单个实例成功而全局改为已验证。

镜像中只保存依赖、程序、工作流和软链接，不保存 `.state`、一次性凭证、用户输入/输出、日志或真实环境配置。必须用新实例分别验证两个区域的挂载和冷启动，验证通过后再制作/发布镜像。

官方依据（2026-09-26 核对）：[公共模型库与软链接](https://compshare.cn/docs/operation/gpu/usepublicmodel)、[只读实例列表接口](https://compshare.cn/docs/gpus/instance/describecompshareinstance)。官方说明与示例同时出现 `/models` 和 `/model`，因此以实例真实文件为准。


## 本机 Linux 验收（此前阶段）

协议 2.0 验收：Worker 15/15 测试通过（含真实 FFmpeg 截取/抽音轨、符号链接、鉴权及输入校验）；Server 全量 13/13，通过直接运行 `deploy/run.sh` 的接入、文本输出、身份文件 0600 权限、重启身份复用，以及 Server 不监听入站端口仍可执行和归档的专项。测试 ComfyUI 为模拟服务，没有启动真实 ComfyUI 或加载模型。并行重负载时曾出现短租约测试超时，打包结束后的专项和全量复测通过，未放宽断言或租约。

三端仓库同级放置后，在 Server 仓库执行 `bash scripts/accept-linux.sh` 可重跑。本轮安装环境与数据在本机 WSL 独立目录，不修改用户 Server 数据；未申请云端资源。

## IndexTTS 2.5 独立环境

安装固定上游、24 个公共模型文件软链接及真实语音验收见 [语音部署](speech.md)。上海二 A 已实测中文合成、情绪模式、语速、取消和归档；不代表混合 ComfyUI/IndexTTS 显存切换或其他区域通过。
