# 优云智算部署准备

目标为上海二 A、华北二 A 的容器实例。部署到专用目录，不修改其他项目的 ComfyUI 或 Python 环境。本次查询实例列表为 0。安装/启动脚本、完整 Worker 与 CPU/协议链路已在本机 WSL Ubuntu 22.04、Python 3.12.13 验收；尚未在云实例执行，不代表云端网络、公共模型挂载或 GPU 推理通过。

## 1. 安装独立 Worker

在已有实例克隆本仓库，安装 uv 后运行 `bash deploy/install.sh`。使用单独 Python 3.12 环境和 `uv.lock`，不会修改镜像自带的 Python 3.10，也不安装 Torch 或下载模型。FFmpeg 使用实例已有版本；若未安装，不会发布 CPU 媒体能力。

## 2. 链接公共权重

依据实际挂载检查 `deploy/models.example.json`。只列举 Qwen 2512、2.1 的明确文件路径，不递归链接整个公共库。示例来自目录快照，不保证当前实例具有这些文件；缺失时应修正清单或选择具有权重的区域，工具不会自动下载。

```bash
uv run --frozen zhilume-prepare --manifest deploy/models.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models
# 检查计划后显式创建链接，已有不同文件绝不覆盖：
uv run --frozen zhilume-prepare --manifest deploy/models.example.json --comfy-root /root/ComfyUI --model-root /model --model-root /models --apply-links
```

同名候选对应多个实际文件会报告歧义。需要针对实际目录修正清单，不能静默选择模型版本。软链接不会复制权重进镜像。

## 3. 只读服务检查

为 Worker 配置专用 ComfyUI，仅监听本机；禁止和其他创作界面共享队列。拷贝 `config/comfy.example.json` 为 `config/comfy.local.json`，只保留确实存在的配置。ComfyUI 本身需在 GPU 阶段明确启动，准备脚本不会启动它。

```bash
uv run --frozen zhilume-prepare --server https://your-server.example --comfy-config config/comfy.local.json
```

仅 GET Server `/api/v1/system` 和 ComfyUI `/object_info`。检查协议、节点及配置文件名，不 POST `/prompt`，不加载权重。Server 能访问不代表 WebSocket 或素材回传验收；必须继续下面的接入测试。

云端 Worker 需能主动连接用户的 Server HTTPS / WSS 地址；本地 `127.0.0.1:4310` 无法从云实例访问。通过用户已有公网入口或受控隧道暴露 Server，不把 ComfyUI 或 Worker 开到公网。禁止把 Server 管理凭证、平台 API 密钥写进镜像。

## 4. 先跑协议和 CPU

```bash
export ZHILUME_SERVER=https://your-server.example
export ZHILUME_WORKER_NAME=zhilume-sh2a
read -rs -p '一次性接入凭证: ' ZHILUME_ENROLLMENT; echo
export ZHILUME_ENROLLMENT
bash deploy/run.sh
```

默认 `ZHILUME_ENABLE_IMAGE=0`。接入后在管理台确认心跳、能力、忙碌状态；从 Studio 执行文本回显、素材复制、CPU 截取/抽音轨，检查结果入库。断开再重连、取消、重启中断后手动重试分别验收。身份保存在 `.state`，不要重复注册或把它提交到仓库。

## 5. GPU 阶段与镜像

告知用户开始 GPU 验收后，才显式设置 `ZHILUME_ENABLE_IMAGE=1` 和 `ZHILUME_COMFY_CONFIG`。首先低分辨率验收 2512 文生图、2.1 文生图/编辑/多参考/RGBA，再测取消、OOM、断线恢复和耗时/显存。现有测试服务不能证明这些能力已经可用。

镜像中只保存依赖、程序、工作流和软链接，不保存 `.state`、一次性凭证、用户输入/输出、日志或真实环境配置。必须用新实例分别验证两个区域的挂载和冷启动，验证通过后再制作/发布镜像。

官方依据（2026-09-26 核对）：[公共模型库与软链接](https://compshare.cn/docs/operation/gpu/usepublicmodel)、[只读实例列表接口](https://compshare.cn/docs/gpus/instance/describecompshareinstance)。官方说明与示例同时出现 `/models` 和 `/model`，因此以实例真实文件为准。


## 本机 Linux 验收

2026-09-26：Worker 14/14 测试通过（含真实 FFmpeg 截取/抽音轨、符号链接）；Server 11/11 集成测试通过，另加部署启动脚本专项 1/1。专项直接运行 `deploy/run.sh`，验证注册、文本回传、身份文件 0600 权限，以及不传接入凭证的重启身份复用。测试 ComfyUI 为模拟服务，没有启动真实 ComfyUI 或加载模型。

三端仓库同级放置后，在 Server 仓库执行 `bash scripts/accept-linux.sh` 可重跑。本轮安装环境与数据在本机 WSL 独立目录，不修改用户 Server 数据；未申请云端资源。
