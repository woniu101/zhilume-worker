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

接入后在管理台核对心跳、能力、忙碌状态；执行文本回显、素材复制、CPU 视频截取/抽音轨，并检查结果归档。断开再连、取消、Worker/Server 重启分别验收。每个 Worker 状态目录只绑定一个 Server；Server 保留 worker-connections.json 以复用稳定身份。

网络可达性由用户解决，本项目不实现或引导配置 SSH 隧道、组网、中继。不同可用区的实际访问入口仍需现场验证。

## 5. GPU 阶段与镜像

告知用户开始 GPU 验收后，才显式设置 `ZHILUME_ENABLE_IMAGE=1` 和 `ZHILUME_COMFY_CONFIG`。首先低分辨率验收 2512 文生图、2.1 文生图/编辑/多参考/RGBA，再测取消、OOM、断线恢复和耗时/显存。现有测试服务不能证明这些能力已经可用。

镜像中只保存依赖、程序、工作流和软链接，不保存 `.state`、一次性凭证、用户输入/输出、日志或真实环境配置。必须用新实例分别验证两个区域的挂载和冷启动，验证通过后再制作/发布镜像。

官方依据（2026-09-26 核对）：[公共模型库与软链接](https://compshare.cn/docs/operation/gpu/usepublicmodel)、[只读实例列表接口](https://compshare.cn/docs/gpus/instance/describecompshareinstance)。官方说明与示例同时出现 `/models` 和 `/model`，因此以实例真实文件为准。


## 本机 Linux 验收

协议 2.0 验收：Worker 15/15 测试通过（含真实 FFmpeg 截取/抽音轨、符号链接、鉴权及输入校验）；Server 全量 13/13，通过直接运行 `deploy/run.sh` 的接入、文本输出、身份文件 0600 权限、重启身份复用，以及 Server 不监听入站端口仍可执行和归档的专项。测试 ComfyUI 为模拟服务，没有启动真实 ComfyUI 或加载模型。并行重负载时曾出现短租约测试超时，打包结束后的专项和全量复测通过，未放宽断言或租约。

三端仓库同级放置后，在 Server 仓库执行 `bash scripts/accept-linux.sh` 可重跑。本轮安装环境与数据在本机 WSL 独立目录，不修改用户 Server 数据；未申请云端资源。
