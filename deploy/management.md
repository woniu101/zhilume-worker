# Worker 0.9 安装与本机管理

目标系统为 Linux / WSL2。Windows 可运行控制服务及 CPU 测试，但 Windows 原生 GPU 推理未纳入本版验收。部署机器不需要 Node.js；React 静态页面已放入 wheel，由一个 FastAPI/Uvicorn 进程提供。

## 分离程序和数据

| 目录示例 | 内容 | 升级/镜像规则 |
|---|---|---|
| /opt/zhilume-worker/0.9.0 | 核心 Python 环境和程序 | 每版本新目录；current 指向选定版本 |
| /var/lib/zhilume-worker/config | 执行器和监听配置 | 保留，含本机路径，不复制为通用镜像配置 |
| /var/lib/zhilume-worker/identity.json、credentials、owner | Worker 身份、两类凭证、绑定 | 保留；严禁打入镜像 |
| /srv/zhilume/runtimes | 按执行器分开的既有/新推理环境 | 管理员显式安装和升级 |
| /srv/zhilume/models | 本地模型或公共模型链接 | 不由 Worker 自动下载 |
| /var/lib/zhilume-worker/attempts、logs | 任务缓存/结果与日志 | 不含于发布镜像；失败文件保留用于诊断 |
| /var/lib/zhilume-gpu-locks | 主机公共 GPU 锁及释放隔离标记 | 同机所有 Worker/容器共用，不能按容器分割 |

初版 attempts 同时承担传输缓存与任务临时文件，没有跨项目永久模型缓存。发布 wheel 仅含代码、契约和静态页面，不包含以上运行数据。升级停止旧服务，安装新程序并切换 current，再复用同一 state；不要重新生成身份。系统服务管理器/容器负责 Worker 开机启动和异常重启。

## 1. 显式安装核心

先由管理员准备 Python/uv；安装脚本不会启动服务或下载模型：

```bash
export ZHILUME_WHEEL=/path/to/zhilume_worker-0.9.0-py3-none-any.whl
export ZHILUME_PROGRAM=/opt/zhilume-worker/0.9.0
bash deploy/install.sh
```

核心只有 HTTP、WebSocket、协议校验和管理接口依赖，不默认安装 Torch、IndexTTS、ComfyUI 或 Pillow。图片/视频适配器的轻量图片校验依赖可显式安装到核心环境；大推理依赖留在独立环境：

```bash
uv pip install --python "$ZHILUME_PROGRAM/bin/python" "${ZHILUME_WHEEL}[image,video]"
```

该操作仍不会安装 GPU 推理框架。既有 ComfyUI 和 IndexTTS 可直接配置复用，无需重复安装。

## 2. 启动控制服务

```bash
export ZHILUME_RESOURCE_LOCK_DIR=/var/lib/zhilume-gpu-locks
/opt/zhilume-worker/0.9.0/bin/zhilume-worker --state /var/lib/zhilume-worker --host 127.0.0.1 --port 4320
```

访问 `/management`。在部署机器执行同一命令加 `--show-management-token` 显示管理凭证。`--show-token` 显示另一个任务接入凭证，只供 Server 调度连接使用；两类权限在服务端验证，不能互换。管理页面的公开静态壳不含本机数据。

默认本机监听。远程可达地址和传输保护由部署者提供；本项目不购买实例、不搭 SSH/组网/中继，也不需要 Server 公网地址。Server 主动连接 Worker，Worker 无 Server 连接时仍可配置和诊断。

systemd 示例为 `deploy/zhilume-worker.service`。管理员显式创建对应用户、程序/数据/锁目录及权限，然后安装 unit 并启动；脚本不会自动修改系统服务。采用一个 Uvicorn 进程，不通过 Web 进程数扩充 GPU 并发。容器采用相同状态卷和主机锁卷，退出时回收受管进程。

## 3. 复用环境、检查、启停

环境页选择 image/speech/video，读取现有配置并编辑。示例分别是 `config/comfy.example.json`、`indextts.example.json`、`video.example.json`。须替换全部 identity 占位符，准确声明模型版本、量化、组件 SHA-256 或不可变版本；示例不能直接宣称就绪。

- Qwen/H3 配置已有专用 ComfyUI 地址、模型相对文件名及执行限制。ComfyUI 服务由其已有系统服务/容器负责启动和退出；Worker 只管理适配器的接单、取消和模型卸载，不擅自终止用户所有 ComfyUI 进程。
- IndexTTS 配置独立 Python、仓库、模型目录、工作目录及 `device`（如 cuda:0）。推理进程按任务创建并受管，取消等待进程组退出。
- 当前以本机全部可见 GPU UUID 为保守独占集合；同机多 Worker 不能借进程数量绕过单卡互斥。不是多卡细分调度器。设备映射由部署环境与执行器配置一致提供。
- 保存配置仅保存并停用。检查核对路径、依赖、模型、服务和硬件，不运行模型。启用检查通过且恢复旧任务/确认卸载后才发布能力。
- 停用可选择“完成任务后停用”或“取消任务并停用”，只取消该执行器的任务。释放未确认进入隔离，重启不自动消除标记；修正原执行器服务后重新启用以恢复。
- 管理操作异步执行，HTTP 心跳/查询不等待长安装或推理结束。单个执行器出错只显示原因，不阻止管理入口启动。

命令行使用同一管理逻辑：

```bash
zhilume-worker --state /var/lib/zhilume-worker --executor image --executor-action configure --config-file /path/to/comfy.local.json
zhilume-worker --state /var/lib/zhilume-worker --executor image --executor-action check
zhilume-worker --state /var/lib/zhilume-worker --executor image --executor-action enable
zhilume-worker --state /var/lib/zhilume-worker --executor image --executor-action disable --stop-policy wait
zhilume-worker --state /var/lib/zhilume-worker --executor-action diagnose
```

服务运行时 CLI 调用管理 API 并返回异步 operationId；面板或 GET `/management/api/operations` 查看完成状态。服务未运行时 CLI 使用独占状态目录锁；同一数据目录禁止第二个服务/CLI 并行修改。CLI enable 可保存启动意图，离线 CLI 结束后并无常驻执行器，需启动 Worker 服务。

## 4. 按需安装标准推理环境

优先配置和检查已有环境。环境页折叠区“安装独立标准环境”先显示命令计划，点击“执行依赖安装”才下载程序和依赖；新目录安装，不覆盖已有环境。CLI 共用 `installer.py`：

```bash
zhilume-install --executor image --directory /srv/zhilume/runtimes/comfy-new --python /usr/bin/python3
# 核对计划后在同一命令末尾显式加 --execute
```

固定 ComfyUI / IndexTTS 源码修订；image/video 可指向同一个既有专用 ComfyUI，无需重复安装。安装不会下载权重、配置软链接、启动服务或提交 GPU 推理，完成状态是 installed-unchecked。IndexTTS 安装需要 uv，ComfyUI 需要支持 venv/pip 的 Python，均需 git；安装失败保留 incomplete 标记和日志。

依赖安装的网络与 CUDA/Torch 兼容性仍须在目标 GPU 镜像验证；本机 Linux 核心干净安装通过，不等于完整推理环境安装已通过。模型下载若公共库缺失，须由用户单独明确执行。

## 5. 云平台镜像准备与真实测试

优先上海二 A，再独立验证华北二 A；区域库存和公共模型挂载每次现场核对。`zhilume-prepare` 的精确清单软链接是部署工具，平台路径不参与 Worker 调度代码。目录、链接和执行器检查分别执行。

程序和运行环境装入专用版本目录；公共模型使用经过检查的精确链接。制作镜像前检查镜像只含程序/标准环境/无身份的示例配置：排除 state、身份、凭证、绑定、日志、缓存、attempts、测试媒体，以及任意环境变量密钥。镜像第一次启动产生全新身份，不复制已有 Worker 数据目录。

GPU 验收另行显式执行：Qwen/IndexTTS/H3 成功样本、混合模型切换、取消后释放、释放失败隔离、异常重启及多 Worker 同 GPU 互斥。先通知用户再运行。旧版 GPU 报告仅为模型能力证据，不能覆盖 0.9 新控制/资源实现。

诊断页提供各执行器状态、结构化事件日志和脱敏 JSON；不导出 API Key、任务接入凭证或绑定标识。真实推理标记仅在本次执行器会话成功执行并由 Server 归档后成立，重启/重新配置重置；它不是所有尺寸/模式都验收通过的证明。


本轮上海二 A 已验证 Qwen→IndexTTS 切换、图片取消、物理显存回到空载、无 Server 管理和升级身份保留。H3 新版与完整标准依赖安装仍待验收，详见 Server docs/model-scheduling-acceptance-2026-09-27.md。

## 管理页外观

管理页采用与 Studio、Server 相同的中性灰底与雾蓝强调色，统一品牌、侧栏、卡片及控件。右上角可选择浅色、深色或跟随系统；偏好保存在当前浏览器的 `zhilume.theme`，默认深色。登录页同样支持主题切换，窄屏使用顶部导航。主题只改变显示，不影响执行器配置或任务调度。

## 已有环境表单与检查 Worker 已有环境配置体验

- 引擎名称统一为“Qwen 图片”“H3 视频”“IndexTTS 语音”，界面仍以执行器页管理启停。Qwen/H3 标注 ComfyUI 服务，IndexTTS 标注独立 Python 进程；模型、推理程序和适配器不混为同一概念。
- 环境页默认结构化表单，按引擎保留未保存草稿，JSON 作为高级编辑入口，两种视图使用同一配置对象。保存与检查、启用分开；更换配置后清除旧检查结论。
- Qwen/H3 配置已有专用 ComfyUI URL、独占确认、超时、释放上限和多模型执行规格。Python、ComfyUI 程序/模型目录、GPU 设备由外部 ComfyUI 服务启动配置决定，不提供不生效的本机字段。两种适配器可以指向同一专用服务。
- IndexTTS 配置独立 Python、程序目录、模型目录、工作目录、FFmpeg、设备及能力限制。空工作目录使用程序目录。所有路径指 Worker 部署机器，非浏览器机器。
- 模型表单包含组件文件、权重版本、量化、固定组件标识及输入输出限制；模板不伪造模型身份，不自动下载。原有不可变规格匹配规则保持不变。
- 只读检查逐项显示 checking/passed/failed/skipped、原因及处理建议：服务可达性、路径/软链接、基础 Python 依赖、规格、GPU、模型文件与工作流。缺 GPU 不阻止其余检查；检查不加载模型、不执行推理、不下载依赖。
- 部署操作显示开始/结束时间、进行中/完成/失败与具体检查阶段，不编造耗时百分比；每个引擎可读取最近 60 条脱敏部署事件。安装/检查/启停日志不冒充原始推理控制台日志。
- 管理接口继续独立鉴权。新增模板与日志端点同样拒绝任务接入凭证。日志写入排除后台 task 对象，重启后可读；环境错误不破坏管理服务。
