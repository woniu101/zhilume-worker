# Worker 0.10：通用 Linux / WSL2 部署

核心与推理环境分开：只安装核心即可使用管理台；不需要 Node.js、Torch、ComfyUI、Server 或显卡。真实生成仍需准备对应 GPU 环境。上海二 A 已通过复用已有推理环境的托管模式真实 GPU 验收；Windows 原生推理、全新推理环境安装和其他云 GPU 仍未验收。

## 1. 安装核心和启动管理台

准备 Python 3.11+（建议 3.12）、uv，解压 `zhilume-worker-0.10.0-linux-bundle.tar.gz` 即可获得发布 wheel、安装入口和配置模板，无需克隆开发仓库。也可使用源码树与单独 wheel。安装依赖是显式操作，安装器不会自动安装系统 Python、启服务或改 systemd。

```bash
# 第一次可用系统 python3 执行 bootstrap；--python 指向预先准备的 3.12。
bash deploy/install.sh install --program /opt/zhilume-worker --state /var/lib/zhilume-worker \
  --wheel /path/to/zhilume_worker-0.10.0-py3-none-any.whl --python /path/to/python3.12
bash deploy/install.sh activate --program /opt/zhilume-worker --state /var/lib/zhilume-worker --version 0.10.0
/opt/zhilume-worker/current/bin/zhilume-worker --state /var/lib/zhilume-worker --show-management-token
export ZHILUME_RESOURCE_LOCK_DIR=/var/lib/zhilume-gpu-locks
/opt/zhilume-worker/current/bin/zhilume-worker --state /var/lib/zhilume-worker
```

访问 `/management`，输入部署管理凭证。另用 `--show-token` 显示 Server 任务接入凭证；后者无部署管理权限。默认本机监听，外部访问路径由部署者自行解决，无需 Server 公网地址。

目录为 `program/versions/<版本>/venv` 与 `program/current`；身份、凭证、配置、缓存和日志只在 state。程序与 state 不允许互相嵌套。安装失败保留 incomplete 记录，不能激活；不覆盖已有版本目录。每个成功版本记录 wheel SHA-256 和实际已安装依赖清单。

图片/视频适配器需显式安装轻量 Pillow 校验依赖到当前核心环境，推理依赖仍保持独立：

```bash
uv pip install --python /opt/zhilume-worker/current/bin/python '/path/to/zhilume_worker-0.10.0-py3-none-any.whl[image,video]'
```

## 2. 选择环境管理方式

| 方式 | 配置 | 由谁启动/停止推理程序 |
|---|---|---|
| 复用已有服务 | 执行器填 ComfyUI URL | 原 systemd、容器或用户；Worker 不接管进程 |
| Worker 托管服务 | 环境页先保存运行服务，再让图片/视频选择同一个 runtimeId | Worker 创建并管理独立进程组 |
| IndexTTS | 独立 Python、程序及模型路径 | Worker 已按任务创建并回收进程组 |

托管 ComfyUI 先准备已有环境，填写 Python、程序目录、可选 extra_model_paths YAML、可见 GPU 序号和端口。路径均相对于 Worker 机器。示例 `runtime.linux.example.json`、`model-paths.linux.example.yaml`；直接配置任意平台的本地模型目录，不依赖优云公共库。优云可使用 `models.example.json` 等明确软链接清单，不把 /model 写死在业务实现中。

明确步骤：保存运行环境 → 检查程序路径 → 显式启动服务 → 保存模型执行器配置 → 检查环境/模型 → 启用接单 → 单独真实推理验收。路径检查不导入模型，不证明依赖或真实推理通过。

托管服务只监听本机、禁用自定义节点和 xFormers；与已验收内置 Qwen/H3 工作流一致。不提供任意 shell 命令编辑框。图片和 H3 共用同一个运行环境，不重复创建 ComfyUI。

停止共享服务先停止所有关联执行器接单，可选择等待任务结束或取消；取消后等待资源释放。释放失败仍保留 GPU 隔离标记，停止进程不等于解除隔离。可以重新启动同一受管环境，再检查并启用原执行器恢复；其他 Worker 不得清除该标记。端口已占用时不会接管或终止未知进程。

Worker 正常关闭时回收所有自建服务进程组；异常死亡由 systemd KillMode=control-group 或容器 init 负责回收。重启后托管服务默认停止，用户须显式启动，再启用执行器；不隐式重跑推理或复用未知 PID。外部 ComfyUI 不受影响。

CLI 与页面共用管理 API，需要先启动核心服务：

```bash
zhilume-worker --state /var/lib/zhilume-worker --runtime comfy-main --runtime-action configure --config-file /path/to/runtime.json
zhilume-worker --state /var/lib/zhilume-worker --runtime comfy-main --runtime-action check
zhilume-worker --state /var/lib/zhilume-worker --runtime comfy-main --runtime-action start
zhilume-worker --state /var/lib/zhilume-worker --runtime comfy-main --runtime-action stop --stop-policy wait
```

## 3. 升级、回退和系统服务

用相同 program/state 执行 install 安装新 wheel；停止旧 Worker 后执行 activate。服务仍在运行时会因 state 锁拒绝切换。`rollback` 切回上一个已安装版本，身份与数据不动；不删除任何版本目录。当前初版数据格式不作跨未来不兼容版本回退保证，发布说明必须明确变化。

`zhilume-deploy service --program ... --state ... --user zhilume --lock-directory ...` 生成 systemd unit 文本，管理员显式安装。先准备用户及程序、数据、公共锁目录权限。`current` 指向虚拟环境；单 Uvicorn 进程运行。使用 status 查看实际链接版本。不要给多个 Worker 配置各自独立 GPU 锁目录。

`deploy/Dockerfile` 和 `compose.yaml` 是仅含控制服务的容器模板，不宣称包含 GPU 推理环境。持久化 state，共享宿主机锁目录并确保 UID 10001 可写。要在容器内推理，需另外构建/安装 CUDA 兼容环境、映射 GPU/模型；不可假设直接挂载宿主机 Python 虚拟环境就兼容。模板尚未完成容器实机验收，不作为已验证 GPU 镜像发布。

## 4. 验收边界

- 本机 Windows：管理 API、权限、故障隔离与现有任务回归；托管启动明确拒绝 Windows 原生。
- WSL2 Ubuntu 22.04：受管进程、子进程回收、取消启动、拒绝占用端口、版本切换及数据保留专项。
- `deploy/accept-portable.py`：显式安装前后两个核心 wheel，在全新目录启动管理台，校验无 Torch/Pillow、未绑定 Server 可用、运行中拒绝升级、回退及身份保留。只运行 CPU 核心，不安装推理依赖。
- 标准 ComfyUI/IndexTTS 安装仍须目标 GPU 环境验收。安装器固定上游修订并保存实际依赖清单；清单属于安装溯源，不等于完整 CUDA 依赖锁或推理兼容性证书。
- 上海二 A RTX 5090：0.10.0 wheel 安装并沿用 state，Qwen/H3 共用托管 ComfyUI，IndexTTS 独立环境。图片→语音→H3 文生视频均真实生成并归档，显存回到 780–828 MiB；视频运行时取消并停止共享服务后为 2 MiB。服务恢复后再次生成成功；正常重启 Worker 回收受管进程，身份/凭证/绑定不变，无 Server 时仍可启动和检查。
- 推理期间管理接口采样 50 次，P95 614 ms，无探测失败；Server 不监听入站端口。结果仅代表本次单卡样本。H3 Ref2VA/首尾帧本轮未重测，硬崩溃及运行中等待自然完成再停服尚待验收。
- 其他云平台、自有 NVIDIA Linux 主机、容器 GPU、华北二 A 仍待实测。不得以本次复用环境样本替代全新安装或跨平台验收。完整记录见 Server 仓库 `docs/worker-managed-gpu-acceptance-2026-09-27.md`。

语言模型执行器放在此部署基础之后，首验候选 Qwen3.5-9B；本版本尚未实现语言模型推理适配器。
