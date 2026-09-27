# 标准推理环境安装（Worker 0.11）

**当前状态：候选依赖方案，尚未完成干净环境 GPU 验收。** 0.10 在上海二 A 复用已有环境的成功结果，不能替代本方案的安装与推理验证。

## 目录与前置条件

- Linux / WSL2 x86_64、glibc 2.28+；自行准备 Git、uv（当前测试工具版本 0.11.19）。IndexTTS 还需 FFmpeg。安装器不修改系统、不安装 Python、驱动或系统软件包。
- Worker 核心建议 Python 3.12；ComfyUI 标准环境使用 Python 3.12；IndexTTS 标准环境使用 Python 3.11（上游要求 `<3.12`）。允许不同绝对路径指向不同 Python。
- 安装到尚不存在的新目录，至少预留 24 GiB。此数值是安装前最低检查值，不是峰值空间保证。`source/`、`venv/`、`package-cache/` 分开，包缓存位于同一磁盘；模型放在单独模型目录。安装目录必须与 Worker state 分离。
- Qwen 与 H3 使用同一个 ComfyUI 环境，在 Worker 环境页关联同一个 runtimeId；无须各装一份。IndexTTS 使用独立环境。

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

## 后续验收

先在上海二 A 新目录安装两套推理环境，配置公共模型链接，重跑 Qwen、H3、IndexTTS 及取消/切换；通过后才更新本方案的 GPU 验证状态。然后再以基础镜像验证整机部署。镜像不包含 state、凭证、绑定、缓存日志或测试素材，不把平台模型根目录写进 Worker 业务代码。
