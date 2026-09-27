import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Cpu,
  HardDrive,
  LayoutDashboard,
  Link,
  FileText,
  SlidersHorizontal,
  ShieldCheck,
  RefreshCw,
  ArrowRight,
  ChevronRight,
  LogOut,
  LoaderCircle,
  X,
} from "lucide-react";
import { Brand, ThemePicker, executorNames, Status, statusNames } from "./ui";
import "./style.css";
let token = sessionStorage.getItem("worker-management") || "";
async function api(path: string, method = "GET", body?: any) {
  const response = await fetch("/management/api/" + path, {
    method,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || "请求失败");
  return data;
}
function App() {
  const [busy, setBusy] = useState(false),
    [reachable, setReachable] = useState(false);
  const [connected, setConnected] = useState(false),
    [credential, setCredential] = useState(token),
    [data, setData] = useState<any>(null),
    [page, setPage] = useState("overview");
  const [error, setError] = useState(""),
    [operations, setOperations] = useState<any[]>([]),
    [config, setConfig] = useState(""),
    [kind, setKind] = useState("image"),
    [access, setAccess] = useState<any>(null),
    [report, setReport] = useState<any>(null);
  const refreshing = useRef(false);
  const refresh = async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const [d, o] = await Promise.all([api("overview"), api("operations")]);
      setData(d);
      setOperations(o);
      setConnected(true);
      setReachable(true);
    } finally {
      refreshing.current = false;
    }
  };
  useEffect(() => {
    if (!token) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh();
      } catch (e) {
        if (!stopped) {
          setReachable(false);
          setError((e as Error).message);
        }
      }
      if (!stopped) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [connected]);
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function act(path: string, body: any = {}, method = "POST") {
    try {
      setError("");
      await api(path, method, body);
      await refresh();
      if (path === "access") setAccess(await api("access"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const [installDir, setInstallDir] = useState(""),
    [python, setPython] = useState(""),
    [installPlan, setInstallPlan] = useState<any>(null);
  const names: Record<string, string> = {
    overview: "概览",
    executors: "执行器",
    environment: "环境",
    access: "接入",
    diagnostics: "诊断",
  };
  const descriptions: Record<string, string> = {
    overview: "查看当前机器的服务、资源与任务状态。",
    executors: "按需管理推理环境，独立检查与启停。",
    environment: "复用已有推理环境，配置程序与模型位置。",
    access: "管理当前 Worker 的监听设置与 Server 绑定。",
    diagnostics: "汇总执行器状态与日志，导出脱敏诊断报告。",
  };
  const icons: Record<string, typeof Cpu> = {
    overview: LayoutDashboard,
    executors: Cpu,
    environment: SlidersHorizontal,
    access: Link,
    diagnostics: FileText,
  };
  function changePage(next: string) {
    setPage(next);
    if (next === "access") void run(async () => setAccess(await api("access")));
    if (next === "environment")
      setConfig(
        JSON.stringify(
          data.executors.find((x: any) => x.id === kind)?.config || {},
          null,
          2,
        ),
      );
  }
  return (
    <div className={connected ? "app-shell" : "login-shell"}>
      {connected && (
        <aside className="sidebar">
          <Brand />
          <div className="machine-card">
            <span className="eyebrow">当前机器</span>
            <strong title={data.name}>{data.name}</strong>
            <span>
              <i className={`status-dot ${reachable ? "online" : ""}`} />
              {reachable ? "管理服务已连接" : "连接已中断"}
            </span>
          </div>
          <nav aria-label="Worker 管理">
            {Object.entries(names).map(([k, v]) => {
              const Icon = icons[k];
              return (
                <button
                  className={page === k ? "active" : ""}
                  aria-current={page === k ? "page" : undefined}
                  key={k}
                  onClick={() => changePage(k)}
                >
                  <Icon size={18} />
                  {v}
                </button>
              );
            })}
          </nav>
          <div className="sidebar-bottom">
            <div className="local-note">
              <ShieldCheck size={18} />
              <span>
                独立部署管理<small>v{data.version} · 当前机器</small>
              </span>
            </div>
            <button
              className="quiet"
              onClick={() => {
                token = "";
                sessionStorage.removeItem("worker-management");
                window.location.reload();
              }}
            >
              <LogOut size={16} />
              退出管理台
            </button>
          </div>
        </aside>
      )}
      <div className="workspace">
        <header className="topbar">
          {connected ? (
            <div className="breadcrumb">
              Worker 管理台
              <ChevronRight size={14} />
              <span>{names[page]}</span>
            </div>
          ) : (
            <Brand />
          )}
          <ThemePicker />
        </header>
        {!connected ? (
          <main className="login-main">
            <section className="login-card">
              <div className="section-icon">
                <ShieldCheck size={24} />
              </div>
              <p className="eyebrow">YOUR CREATIVE INFRASTRUCTURE</p>
              <h1>连接 Worker 管理台</h1>
              <p className="description">配置你的执行环境，让创作随时就绪。</p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  token = credential;
                  sessionStorage.setItem("worker-management", token);
                  void run(refresh);
                }}
              >
                <label>
                  部署管理凭证
                  <input
                    type="password"
                    aria-label="部署管理凭证"
                    placeholder="输入此机器的管理凭证"
                    autoComplete="off"
                    value={credential}
                    onChange={(e) => setCredential(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || !credential.trim()}
                >
                  {busy ? (
                    <LoaderCircle size={17} className="spin" />
                  ) : (
                    <ArrowRight size={17} />
                  )}
                  连接
                </button>
              </form>
              <div className="login-help">
                在部署机器执行以下命令获取凭证
                <code>
                  zhilume-worker --state /path/to/state --show-management-token
                </code>
              </div>
              <p className="footnote">
                部署管理凭证与 Server 任务接入凭证独立。
              </p>
            </section>
          </main>
        ) : (
          <main className="content">
            <div className="page-heading">
              <div>
                <p className="eyebrow">WORKER / {page.toUpperCase()}</p>
                <h1>{names[page]}</h1>
                <p className="description">{descriptions[page]}</p>
              </div>
              <button
                className="icon-button"
                aria-label="刷新状态"
                title="刷新状态"
                disabled={busy}
                onClick={() => void run(refresh)}
              >
                <RefreshCw size={17} className={busy ? "spin" : ""} />
              </button>
            </div>
            {page === "overview" && data && (
              <>
                <div className="metrics">
                  <article className="metric">
                    <div className="metric-label">
                      管理服务
                      <Activity size={18} />
                    </div>
                    <strong>{reachable ? "服务在线" : "连接中断"}</strong>
                    <small>
                      v{data.version} · {data.name}
                    </small>
                  </article>
                  <article className="metric">
                    <div className="metric-label">
                      Server 接入
                      <Link size={18} />
                    </div>
                    <strong>
                      {data.serverConnected
                        ? "Server 已连接"
                        : data.bound
                          ? "已绑定 · 未连接"
                          : "未绑定"}
                    </strong>
                    <small>独立配置与诊断始终可用</small>
                  </article>
                  <article className="metric">
                    <div className="metric-label">
                      可用执行器
                      <Cpu size={18} />
                    </div>
                    <strong>
                      {
                        data.executors.filter((x: any) => x.state === "ready")
                          .length
                      }
                      <em>/ {data.executors.length}</em>
                    </strong>
                    <small>已启用且可接收任务</small>
                  </article>
                  <article className="metric">
                    <div className="metric-label">
                      可用磁盘
                      <HardDrive size={18} />
                    </div>
                    <strong>
                      {(data.disk.free / 1024 ** 3).toFixed(1)}
                      <em>GB</em>
                    </strong>
                    <small>
                      总容量 {(data.disk.total / 1024 ** 3).toFixed(1)} GB
                    </small>
                  </article>
                </div>
                <div className="overview-grid">
                  <section className="panel">
                    <div className="section-heading">
                      <h2>
                        <Cpu size={18} />
                        GPU 与资源
                      </h2>
                      <span className="muted">{data.gpus.length} 张 GPU</span>
                    </div>
                    {data.gpus.length ? (
                      data.gpus.map((g: any) => (
                        <div className="gpu" key={g.uuid}>
                          <strong>{g.name}</strong>
                          <p className="mono muted">{g.uuid}</p>
                          <div className="split">
                            <span>显存使用</span>
                            <span>
                              {g.usedMiB} / {g.totalMiB} MiB
                            </span>
                          </div>
                          <progress
                            aria-label={`${g.name} 显存使用`}
                            value={Number(g.usedMiB)}
                            max={Number(g.totalMiB) || 1}
                          />
                        </div>
                      ))
                    ) : (
                      <div className="empty-state">
                        <Cpu size={30} />
                        <strong>未发现 NVIDIA GPU</strong>
                        <p>管理服务可正常使用。你仍可配置环境、接入与诊断。</p>
                      </div>
                    )}
                    {data.resourceQuarantined && (
                      <p className="inline-alert" role="alert">
                        GPU 资源释放未确认，已隔离；请检查推理服务后重新启用。
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="section-heading">
                      <h2>
                        <Activity size={18} />
                        当前任务
                      </h2>
                      <span className="muted">{data.tasks.length} 个任务</span>
                    </div>
                    {data.tasks.length ? (
                      data.tasks.map((t: any) => (
                        <div className="task" key={t.jobId}>
                          <Status state={t.cancelling ? "draining" : "ready"}>
                            {t.cancelling ? "取消中" : "执行中"}
                          </Status>
                          <code>{t.jobId}</code>
                        </div>
                      ))
                    ) : (
                      <div className="empty-state">
                        <Activity size={30} />
                        <strong>当前没有任务</strong>
                        <p>任务由 Server 调度，执行进度将在这里显示。</p>
                      </div>
                    )}
                  </section>
                </div>
                <section className="panel">
                  <div className="section-heading">
                    <h2>执行器状态</h2>
                    <button
                      className="text-button"
                      onClick={() => changePage("executors")}
                    >
                      管理执行器
                      <ArrowRight size={15} />
                    </button>
                  </div>
                  <div className="executor-summary">
                    {data.executors.map((x: any) => (
                      <div className="summary-item" key={x.id}>
                        <span className="section-icon">
                          <Cpu size={20} />
                        </span>
                        <div>
                          <strong>{executorNames[x.id]}</strong>
                          <small>
                            真实推理：
                            {x.inferenceVerified ? "已通过" : "尚未验证"}
                          </small>
                        </div>
                        <Status state={x.state}>
                          {statusNames[x.state] || x.state}
                        </Status>
                      </div>
                    ))}
                  </div>
                </section>
                <p className="footnote">
                  <ShieldCheck size={15} />
                  服务在线、环境检查与真实推理验证分别记录。
                </p>
              </>
            )}
            {page === "executors" &&
              data?.executors.map((x: any) => (
                <article className="executor-card" key={x.id}>
                  <h2>
                    <Cpu size={20} />
                    {
                      {
                        image: "Qwen / ComfyUI",
                        speech: "IndexTTS",
                        video: "H3",
                      }[x.id as "image"]
                    }
                  </h2>
                  <div className="verification">
                    <Status state={x.state}>
                      状态：{statusNames[x.state] || x.state}
                    </Status>
                    <span>
                      真实推理：{x.inferenceVerified ? "已通过" : "尚未验证"}
                    </span>
                    <span>可用模型规格：{x.profiles?.length || 0}</span>
                  </div>
                  {x.reason && (
                    <p role={x.state === "error" ? "alert" : "status"}>
                      {x.reason}
                    </p>
                  )}
                  <div className="actions">
                    <button onClick={() => void act(`executors/${x.id}/check`)}>
                      检查环境与模型
                    </button>
                    <button
                      className="primary"
                      onClick={() => void act(`executors/${x.id}/enable`)}
                    >
                      启用
                    </button>
                    <button
                      onClick={() =>
                        void act(`executors/${x.id}/disable`, {
                          policy: "wait",
                        })
                      }
                    >
                      完成任务后停用
                    </button>
                    <button
                      className="danger-quiet"
                      onClick={() =>
                        void act(`executors/${x.id}/disable`, {
                          policy: "cancel",
                        })
                      }
                    >
                      取消任务并停用
                    </button>
                  </div>
                  <small>
                    启用不会执行测试推理。停用必须确认进程停止和资源释放。
                  </small>
                </article>
              ))}
            {page === "environment" && (
              <article>
                <h2>
                  <SlidersHorizontal size={18} />
                  已有环境
                </h2>
                <p className="description">
                  复用已有环境；程序、模型、Python
                  与工作目录由配置指定。保存不会安装依赖、下载模型或运行 GPU。
                </p>
                <label>
                  执行器
                  <select
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value);
                      setInstallPlan(null);
                      setConfig(
                        JSON.stringify(
                          data.executors.find(
                            (x: any) => x.id === e.target.value,
                          )?.config || {},
                          null,
                          2,
                        ),
                      );
                    }}
                  >
                    {["image", "speech", "video"].map((k) => (
                      <option key={k} value={k}>
                        {executorNames[k]}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  onClick={() =>
                    setConfig(
                      JSON.stringify(
                        data.executors.find((x: any) => x.id === kind)
                          ?.config || {},
                        null,
                        2,
                      ),
                    )
                  }
                >
                  读取当前配置
                </button>
                <label>
                  部署配置 JSON
                  <textarea
                    spellCheck={false}
                    rows={13}
                    value={config}
                    onChange={(e) => setConfig(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  onClick={() => {
                    try {
                      void act(
                        `executors/${kind}/config`,
                        JSON.parse(config),
                        "PUT",
                      );
                    } catch {
                      setError("JSON 格式无效");
                    }
                  }}
                >
                  保存配置并停用
                </button>
                <details>
                  <summary>安装独立标准环境（Linux / WSL2）</summary>
                  <p>
                    只下载程序与依赖；不下载模型、不启动
                    GPU。安装使用新目录，现有环境保持独立。
                  </p>
                  <label>
                    新安装目录
                    <input
                      value={installDir}
                      onChange={(e) => {
                        setInstallDir(e.target.value);
                        setInstallPlan(null);
                      }}
                    />
                  </label>
                  <label>
                    已有 Python 绝对路径
                    <input
                      value={python}
                      onChange={(e) => {
                        setPython(e.target.value);
                        setInstallPlan(null);
                      }}
                    />
                  </label>
                  <button
                    onClick={() =>
                      void api("install", "POST", {
                        kind,
                        directory: installDir,
                        python,
                      }).then(setInstallPlan, (e) => setError(e.message))
                    }
                  >
                    查看安装计划
                  </button>
                  {installPlan && (
                    <>
                      <pre>{JSON.stringify(installPlan, null, 2)}</pre>
                      <button
                        onClick={() =>
                          void act("install", {
                            kind: installPlan.kind,
                            directory: installPlan.directory,
                            python,
                            execute: true,
                          })
                        }
                      >
                        执行依赖安装
                      </button>
                    </>
                  )}
                </details>
              </article>
            )}
            {page === "access" && access && (
              <article>
                <h2>
                  <Link size={18} />
                  监听与绑定设置
                </h2>
                <label>
                  名称
                  <input
                    value={access.name}
                    onChange={(e) =>
                      setAccess({ ...access, name: e.target.value })
                    }
                  />
                </label>
                <label>
                  监听地址
                  <input
                    value={access.host}
                    onChange={(e) =>
                      setAccess({ ...access, host: e.target.value })
                    }
                  />
                </label>
                <label>
                  端口
                  <input
                    type="number"
                    value={access.port}
                    onChange={(e) =>
                      setAccess({ ...access, port: +e.target.value })
                    }
                  />
                </label>
                <button
                  onClick={() =>
                    void act("access", {
                      action: "configure",
                      name: access.name,
                      host: access.host,
                      port: access.port,
                    })
                  }
                >
                  保存（监听变更需重启服务）
                </button>
                <div className="access-credentials">
                  <h2>
                    <ShieldCheck size={18} />
                    凭证与绑定
                  </h2>
                  <p className="description">
                    任务接入凭证供 Server 使用，不授予本机部署管理权限。
                  </p>
                  <label>
                    任务接入凭证
                    <input type="password" readOnly value={access.credential} />
                  </label>
                  <button
                    onClick={() =>
                      void run(() =>
                        navigator.clipboard.writeText(access.credential),
                      )
                    }
                  >
                    复制接入凭证
                  </button>
                  <p>绑定 Server：{access.boundServerId || "无"}</p>
                  <button
                    onClick={() => void act("access", { action: "unbind" })}
                  >
                    解除绑定
                  </button>
                  <button
                    onClick={() => void act("access", { action: "rotate" })}
                  >
                    更换接入凭证并解除绑定
                  </button>
                </div>
              </article>
            )}
            {page === "diagnostics" && (
              <article>
                <h2>
                  <FileText size={18} />
                  脱敏诊断报告
                </h2>
                <p className="description">
                  汇总当前机器与执行器的信息，用于排查环境和接入问题。无 Server
                  连接时也可使用。
                </p>
                <button
                  className="primary"
                  onClick={() =>
                    void api("diagnostics").then(setReport, (e) =>
                      setError(e.message),
                    )
                  }
                >
                  生成脱敏诊断报告
                </button>
                {!report && (
                  <div className="empty-state">
                    <FileText size={30} />
                    <strong>尚未生成报告</strong>
                    <p>生成后可查看与下载诊断内容。</p>
                  </div>
                )}
                {report && (
                  <>
                    <pre>{JSON.stringify(report, null, 2)}</pre>
                    <button
                      onClick={() => {
                        const url = URL.createObjectURL(
                          new Blob([JSON.stringify(report, null, 2)], {
                            type: "application/json",
                          }),
                        );
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = "worker-diagnostics.json";
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                    >
                      下载报告
                    </button>
                  </>
                )}
              </article>
            )}
            {operations.length > 0 && (
              <article>
                <h2>部署操作</h2>
                {operations.slice(-5).map((o) => (
                  <p key={o.id}>
                    {o.name} · {o.state}
                    {o.error ? " · " + o.error : ""}
                  </p>
                ))}
              </article>
            )}
          </main>
        )}
      </div>
      {error && (
        <div className="error-toast" role="alert">
          <span>{error}</span>
          <button
            className="icon-button"
            aria-label="关闭错误提示"
            onClick={() => setError("")}
          >
            <X size={17} />
          </button>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
