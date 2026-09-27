"""Worker service entry point. Never calls back into Server."""
import argparse
import logging
import json
import asyncio
import os
import platform
from pathlib import Path
import uvicorn
from .gateway import create_app, identity


def main():
    parser = argparse.ArgumentParser(description="Zhilume Worker 接入服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4320)
    parser.add_argument("--name", default=platform.node())
    parser.add_argument("--state", default=".state")
    parser.add_argument("--show-management-token", action="store_true")
    parser.add_argument("--executor-action", choices=["configure", "check", "enable", "disable", "diagnose"])
    parser.add_argument("--executor", choices=["image", "speech", "video"])
    parser.add_argument("--runtime", help="托管运行环境名称")
    parser.add_argument("--runtime-action", choices=["configure", "check", "start", "stop"])
    parser.add_argument("--config-file")
    parser.add_argument("--stop-policy", choices=["wait", "cancel"], default="wait")
    parser.add_argument("--show-token", action="store_true", help="显示本机 Worker 接入密钥并退出")
    parser.add_argument("--delay", type=float, default=3)
    parser.add_argument("--upload-limit", type=int, default=1024 ** 3)
    parser.add_argument("--comfy-config")
    parser.add_argument("--enable-image-execution", action="store_true")
    parser.add_argument("--speech-config")
    parser.add_argument("--enable-speech-execution", action="store_true")
    parser.add_argument("--video-config")
    parser.add_argument("--enable-video-execution", action="store_true")
    args = parser.parse_args()
    if args.delay < 0 or not 0 <= args.port <= 65535 or args.upload_limit <= 0:
        parser.error("端口、delay 或上传限制无效")
    os.umask(0o077)
    service = Path(args.state) / 'config' / 'service.json'
    if service.exists():
        for key, value in json.loads(service.read_text()).items(): setattr(args, key, value)
    from .management import admin_identity
    if args.show_management_token:
        print(admin_identity(Path(args.state))); return
    if args.runtime_action:
        # Owned long-running processes require a running control service; the CLI
        # does not leave detached inference processes behind when it exits.
        import httpx
        if not args.runtime: parser.error('--runtime-action 需要 --runtime')
        host = '127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host
        with httpx.Client(base_url=f'http://{host}:{args.port}/management/api', headers={'Authorization': 'Bearer ' + admin_identity(Path(args.state))}, timeout=15, trust_env=False) as client:
            if args.runtime_action == 'configure':
                if not args.config_file: parser.error('configure 需要 --config-file')
                response = client.put(f'/runtimes/{args.runtime}', json=json.loads(Path(args.config_file).read_text('utf-8')))
            else:
                response = client.post(f'/runtimes/{args.runtime}/{args.runtime_action}', json={'policy': args.stop_policy})
            response.raise_for_status(); print(json.dumps(response.json(), ensure_ascii=False))
        return
    if args.executor_action:
        from .worker import Worker
        from .resources import StateLock
        if args.executor_action != 'diagnose' and not args.executor: parser.error('此操作需要 --executor')
        lock = StateLock(args.state)
        try: lock.acquire()
        except ValueError:
            # A live service remains the only owner of configuration and processes.
            import httpx
            host = '127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host
            with httpx.Client(base_url=f'http://{host}:{args.port}/management/api', headers={'Authorization': 'Bearer ' + admin_identity(Path(args.state))}, timeout=15, trust_env=False) as client:
                if args.executor_action == 'diagnose': response = client.get('/diagnostics')
                elif args.executor_action == 'configure':
                    if not args.config_file: parser.error('configure 需要 --config-file')
                    response = client.put(f'/executors/{args.executor}/config', json=json.loads(Path(args.config_file).read_text('utf-8')))
                else: response = client.post(f'/executors/{args.executor}/{args.executor_action}', json={'policy': args.stop_policy})
                response.raise_for_status(); print(json.dumps(response.json(), ensure_ascii=False))
            return
        async def manage():
            worker = Worker(args); worker.worker_id = identity(worker.root)['workerId']
            if args.executor_action == 'configure':
                if not args.config_file: parser.error('configure 需要 --config-file')
                await worker.manager.configure(args.executor, json.loads(Path(args.config_file).read_text('utf-8')))
            elif args.executor_action == 'diagnose': print(json.dumps(worker.manager.snapshot(), ensure_ascii=False))
            elif args.executor_action == 'disable': await worker.manager.disable(args.executor, args.stop_policy)
            else: print(json.dumps(await worker.manager.check(args.executor, args.executor_action == 'enable'), ensure_ascii=False))
            await worker.manager.close()
        try: asyncio.run(manage())
        finally: lock.release()
        return
    if args.show_token:
        print(identity(Path(args.state))["credential"])
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(args), workers=1, host=args.host, port=args.port, access_log=False, ws_max_size=256 * 1024, timeout_graceful_shutdown=15)


if __name__ == "__main__":
    main()
