# 标准推理环境安装（Worker 0.11）

**当前状态：已通过上海二 A 全新独立推理环境的安装、基础导入及 Qwen / IndexTTS / H3 FL2VA GPU 流程验收。** 本轮另行测试了管理响应、取消、显存释放与重启。公网转发曾失败，临时验收连接复测通过；两种结果分别保留，不宣称公网链路稳定。详见 [GPU 验收记录](https://github.com/woniu101/zhilume-server/blob/main/docs/worker-standard-gpu-acceptance-2026-09-27.md)。

全新 Ubuntu 宿主缺少工具或解释器时，先按 [宿主准备](ubuntu-host.md) 分步显式准备。已完成另一台全新基础实例的核心部署、两套环境安装与 Studio → Qwen 2512 → 画布实测，见 [全新实例验收](https://github.com/woniu101/zhilume-server/blob/main/docs/worker-fresh-host-acceptance-2026-09-27.md)。

## 目录与前置条件

- Linux / WSL2 x86_64、glibc 2.28+；自行准备 Git、uv（本轮云端测试工具版本 0.12.19）。IndexTTS 还需 FFmpeg。安装器不修改系统、不安装 Python、驱动或系统软件包。
- Worker 核心建议 Python 3.12；ComfyUI 标准环境使用 Python 3.12；IndexTTS 标准环境使用 Python 3.11（上游要求 `<3.12`）。允许不同绝对路径指向不同 Python。
- 安装到尚不存在的新目录，至少预留 24 GiB。此数值是安装前最低检查值，不是峰值空间保证。`source/`、`venv/`、`package-cache/` 分开，包缓存位于同一磁盘；模型放在单独模型目录。安装目录必须与 Worker state 分离。
- Qwen 与 H3 使用同一个 ComfyUI 环境，在 Worker 环境页关联同一个 runtimeId；无须各装一份。IndexTTS 使用独立环境。

## 宿主镜像选择

标准安装不绑定某个云镜像名称或系统 CUDA 目录。先检查 Linux/glibc、解释器、工具、磁盘以及最终 GPU 驱动兼容性，再安装声明的执行规格。不要直接继承镜像中任意版本的 Torch 或系统 site-packages。

| 宿主环境 | 使用方式 | 验收边界 |
| --- | --- | --- |
| CUDA + Python 基础镜像 | 首选；使用明确的 Python 版本创建独立环境 | 可验证从基础镜像部署，仍须单独验证 GPU |
| PyTorch / Miniconda 镜像 | 将其作为宿主，另建 venv 或 conda 环境 | 预装推理依赖不算本安装器的安装结果 |
| 已有 ComfyUI 镜像或实例 | 复用已有专用服务，或在新目录安装固定版本 | 复用验收、全新独立环境验收分别记录，不称为全新操作系统验收 |
| 云端无卡模式 | 可进行依赖下载、安装、基础模块导入、核心管理与协议测试 | 不加载模型、不做 CPU 推理；GPU 工作流、显存释放和驱动兼容性等待有卡验收 |

例如 `cuda130_python312`、`cuda132_python312` 可进入宿主候选检查，不因其名称不同于 `cuda128_python312` 就拒绝。镜像可选、实例库存充足、依赖安装成功、真实推理成功是四个不同结果。宿主镜像的选择不改变模型执行规格或 GPU 能力声明。

## 页面流程

环境 → 安装独立标准环境 → 选择引擎 → 填写新目录及已有 Python → 查看计划 → 显式执行。

计划显示目标平台、Python、目录、步骤、上游修订和依赖锁。修改目录、Python 或引擎后须重新查看；API 执行携带 planId，过期计划被拒绝。

安装前检查操作系统、工具、Python 版本、磁盘余量；通过后才创建目录和下载程序/依赖。页面显示具体阶段，不显示虚假百分比。安装运行中可以取消，回收受管子进程后显示“已取消”。只有管理凭证可以查看计划、执行或取消安装，Server 接入凭证无权操作。

成功状态为 `installed-unchecked`，仅表示依赖一致性和基础模块导入通过，不表示模型可用或 GPU 推理通过。随后手动配置模型目录/链接、托管服务及执行器，分别检查、启动、启用和执行真实生成。

## 命令行

```bash
# 只输出计划，不下载、不安装、不运行模型。
zhilume-install --executor image --directory /opt/zhilume-engines/comfy-new \
  --python /path/to/python3.12

# 显式安装。视频执行器复用此 ComfyUI 环境。
zhilume-install --executor image --directory /opt/zhilume-engines/comfy-new \
  --python /path/to/python3.12 --log /var/log/zhilume/comfy-install.log --execute

zhilume-install --executor speech --directory /opt/zhilume-engines/indextts-new \
  --python /path/to/python3.11 --log /var/log/zhilume/speech-install.log --execute
```

CLI 和页面调用同一安装逻辑。CLI 可用 Ctrl+C 取消；页面通过管理 API 取消。整个安装不下载模型、不启动 ComfyUI、不执行 GPU 推理，也不自动改写已有执行器配置。

## 依赖与故障

- ComfyUI 固定上游提交，使用包内 Linux/Python 3.12/CUDA 12.8 候选锁；包含固定版本及发行包 SHA-256，`uv pip sync --require-hashes` 安装，不在用户机器重新解析“最新依赖”。
- IndexTTS 固定上游提交及 `uv.lock` 哈希，使用 `uv sync --frozen --no-dev`；不默认安装 WebUI、DeepSpeed 等可选组件。
- `installation.json` 记录阶段和状态；`dependencies.lock` 保存本次锁；`installed-requirements.txt` 记录实际依赖。源码构建工具链、系统包和构建隔离依赖没有完整镜像级锁定，不能宣称整机完全可复现。
- 缺工具、Python 不匹配、磁盘不足或计划过期会在下载前失败。网络或依赖安装失败标记为 `incomplete`；取消标记为 `cancelled`。不覆盖或删除既有目录，重试使用新目录；本版不实现断点续装。
- Git 阶段超时 180 秒，依赖安装阶段 3600 秒；失败或取消会清理本安装任务创建的进程组。失败记录和日志保留，不会自动删除供诊断的数据。
- 重启 Worker 不会自动恢复未完成安装。突然断电可能留下 `installing` 记录，此记录不是成功标记；确认旧进程已经结束后使用新目录重试。

## 验收与后续范围

本项目当前开发机没有 GPU，本机 Windows/WSL 仅做轻量开发、协议/管理测试与网络检查，不做大型推理依赖安装、模型加载或推理。验收在云端执行，优先上海二 A、其次华北二 A。优先基础 CUDA/Python 镜像；其他宿主镜像通过前置检查也可使用。GPU 缺货时，可在云端无卡模式的新目录继续安装验收，后续再做有卡推理与全新基础镜像验收，不退回本机执行重任务，也不重装或覆盖已有实例的环境。该开发机约束不影响产品面向有 GPU 的 Linux/WSL2 主机部署。

配置公共模型链接后，分别进行环境检查、Qwen/H3/IndexTTS 真实推理及取消/切换；通过后才更新对应方案的 GPU 验证状态。新实例测试结束后关闭，镜像不包含 state、凭证、绑定、缓存日志或测试素材，不把平台模型根目录写进 Worker 业务代码。云平台购买和测试由项目开发运维执行，不进入 Worker 产品功能。

2026-09-27 标准环境 GPU 样本：2512 / 2.1 文生图、2.1 编辑与双图参考、IndexTTS 中文音色参考、H3 FL2VA 文生视频归档通过；取消视频并停止共享服务后显存约 2 MiB，重新启动生成通过，正常重启保留身份。图片采用低步数，不作为画质基准；未覆盖本轮 Ref2VA、全部情绪、多物理 GPU、自有主机或其他云平台。全局能力声明仍为 unverified，不因单实例验收修改所有部署状态。
