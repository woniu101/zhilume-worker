# Ubuntu 宿主前置准备与标准安装

本页面向操作者准备专用 Linux / WSL2 GPU 主机，不由 Worker 任务接口执行。只需复用已有 Python、Git、uv、FFmpeg 时可跳过对应安装步骤；不要重复安装或覆盖现有推理环境。

## 1. 先检查实际环境

```bash
cat /etc/os-release
uname -m
getconf GNU_LIBC_VERSION
python3 --version
command -v git
command -v uv
command -v ffmpeg
df -h /opt
```

2026-09-27 上海二 A 新建 `cuda132_python312` 平台基础容器实测为 Ubuntu 22.04.5 / glibc 2.35，默认 `python3` 为 3.10.12，没有 Git、uv、FFmpeg。镜像名称不能替代实际检查。ComfyUI 标准方案需要 Python 3.12，IndexTTS 需要 3.11。

有 GPU 的主机另行运行 `nvidia-smi` 检查驱动；该命令不运行推理。没有 GPU 不应阻止核心管理服务部署。

## 2. 显式准备系统工具

下面只适用于有管理权限的 Ubuntu 主机；由操作者决定执行，不是安装 Worker 时的隐式步骤。

```bash
sudo apt-get update
sudo apt-get install git ffmpeg ca-certificates
```

下载失败时先检查包源与代理。不要关闭签名校验或使用 `trusted=yes`。本次基础实例的原 Ubuntu 源下载缓慢，阿里镜像返回 403；改为可达且签名校验通过的 USTC Ubuntu 源后成功。包源配置属于部署环境，不写死在 Worker 中，也不默认改动用户的全局代理。

## 3. 显式准备 uv 和独立 Python

从 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv。本轮验收使用 0.12.19，可复用已有 uv。将解释器放在独立目录，不替换系统 Python：

```bash
export UV_PYTHON_INSTALL_DIR=/opt/zhilume-interpreters
uv python install 3.12.14 3.11.16
uv python find 3.12.14
uv python find 3.11.16
```

这是明确的解释器下载操作。将 `uv python find` 输出的真实路径分别用于后续核心 / ComfyUI 与 IndexTTS 安装参数；两套推理环境不需要相同 Python 路径。安装器本身不会自动下载解释器。

## 4. 安装和检查分开执行

1. 按 [通用部署](portable.md) 从发布包安装核心、初始化独立 state、激活版本。
2. 启动管理服务，确认未绑定 Server 也能配置与诊断；部署机器不需要 Node.js。
3. 按 [标准安装](standard-install.md) 查看安装计划，再显式安装 ComfyUI / IndexTTS 到各自新目录。
4. 依赖安装通过后，配置实际模型目录或公共库软链接，再检查模型、启动服务、启用执行器。
5. 真实 GPU 生成和取消 / 释放是独立验收步骤；`installed-unchecked` 不能当成推理已验证。

系统服务管理器负责 Worker 启停与异常恢复，页面负责执行器。云平台使用 Supervisor 时采用 `stopasgroup=true`、`killasgroup=true` 并设置足够的停止等待时间；不要通过增加 Uvicorn 进程来扩大 GPU 并发。

已验证的单实例结果和未覆盖范围见 [标准环境 GPU 验收](https://github.com/woniu101/zhilume-server/blob/main/docs/worker-standard-gpu-acceptance-2026-09-27.md)。本页不代表 Windows 原生、其他发行版、所有驱动或其他云平台已验收。
