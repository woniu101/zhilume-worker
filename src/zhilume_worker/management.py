"""Deployment authority is independent of the Server scheduling credential."""
import asyncio
import json
import secrets
import shutil
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .resources import hardware
from .environment import defaults, validate_structure


def admin_identity(root):
    folder = root / 'credentials'; folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'management-token'
    if not path.exists():
        with path.open('x') as f: path.chmod(0o600); f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def redact(value):
    if isinstance(value, dict):
        return {k: '[redacted]' if any(s in k.lower() for s in ('key','token','credential','secret','authorization','owner')) else redact(v) for k, v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    if isinstance(value, str) and ('://' in value or len(value) > 1000): return '[redacted]'
    return value


def attach_management(app, worker, credentials, get_owner, unbind):
    token = admin_identity(worker.root)
    operations = {}
    async def close_operations():
        tasks = [v['task'] for v in operations.values() if not v['task'].done()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
    app.state.close_management = close_operations
    def auth(request):
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token): raise HTTPException(401, '需要部署管理凭证；任务接入凭证无此权限')

    def public_operation(operation):
        return {k: v for k, v in operation.items() if k != 'task'}

    def record(operation):
        folder = worker.root / 'logs'; folder.mkdir(exist_ok=True)
        with (folder / (operation['name'].split(':')[0] + '.jsonl')).open('a', encoding='utf-8') as f:
            f.write(json.dumps(redact(public_operation(operation)), ensure_ascii=False) + '\n')

    def launch(name, operation):
        if any(v['state'] == 'running' for v in operations.values()): raise HTTPException(409, '已有部署操作进行中')
        for old in list(operations)[:-49]:
            if operations[old]['state'] != 'running': operations.pop(old)
        oid = secrets.token_hex(12); operations[oid] = {'id': oid, 'name': name, 'state': 'running', 'startedAt': datetime.now(timezone.utc).isoformat()}
        record(operations[oid])
        async def run():
            try:
                result = await operation()
                operations[oid].update(state='failed' if isinstance(result, dict) and result.get('state') == 'error' else 'succeeded', result=result)
            except Exception as e: operations[oid].update(state='failed', error=str(e) if isinstance(e, ValueError) else type(e).__name__)
            except asyncio.CancelledError:
                operations[oid].update(state='failed', error='服务关闭，部署操作已中断')
                raise
            finally:
                operations[oid]['finishedAt'] = datetime.now(timezone.utc).isoformat()
                record(operations[oid])
        operations[oid]['task'] = asyncio.create_task(run())
        return {'operationId': oid}

    @app.get('/management/api/overview')
    async def overview(request: Request):
        auth(request)
        disk = shutil.disk_usage(worker.root)
        return {'version': version('zhilume-worker'), 'workerId': worker.worker_id, 'name': worker.args.name, 'serviceOnline': True,
                'bound': bool(get_owner()), 'serverConnected': bool(worker.socket), 'gpus': await hardware(), 'disk': {'total': disk.total, 'free': disk.free},
                'tasks': [dict(jobId=s['job']['jobId'], cancelling=s.get('cancelling', False)) for s in worker.active.values()],
                'resources': worker.resource_ids, 'resourceQuarantined': bool(worker.quarantined_lease), 'executors': worker.manager.snapshot()}

    @app.get('/management/api/operations')
    async def operation_status(request: Request):
        auth(request); return [public_operation(o) for o in operations.values()]

    @app.get('/management/api/environment-templates')
    async def environment_templates(request: Request):
        auth(request); return defaults()

    @app.get('/management/api/executors/{kind}/logs')
    async def executor_logs(kind: str, request: Request):
        auth(request)
        if kind not in ('image', 'speech', 'video'): raise HTTPException(404, '未知执行器')
        path = worker.root / 'logs' / (kind + '.jsonl')
        if not path.exists(): return []
        def tail():
            with path.open('rb') as f:
                f.seek(0, 2); end = f.tell(); f.seek(max(0, end - 128000))
                lines = f.read().decode('utf-8', errors='replace').splitlines()
            result = []
            for line in lines[-60:]:
                try: result.append(redact(json.loads(line)))
                except ValueError: pass
            return result
        return await asyncio.to_thread(tail)

    @app.put('/management/api/executors/{kind}/config')
    async def configure(kind: str, request: Request):
        auth(request); body = await request.json()
        if len(json.dumps(body)) > 64000: raise HTTPException(413, '配置过大')
        try: validate_structure(kind, body)
        except ValueError as e: raise HTTPException(400, str(e))
        return launch(kind + ':configure', lambda: worker.manager.configure(kind, body))

    @app.post('/management/api/executors/{kind}/{action}')
    async def action(kind: str, action: str, request: Request):
        auth(request)
        if kind not in ('image', 'speech', 'video'): raise HTTPException(404, '未知执行器')
        body = await request.json()
        if action == 'check': return launch(kind + ':check', lambda: worker.manager.check(kind))
        if action == 'enable': return launch(kind + ':enable', lambda: worker.manager.check(kind, True))
        if action == 'disable': return launch(kind + ':disable', lambda: worker.manager.disable(kind, body.get('policy')))
        raise HTTPException(404, '未知操作')

    @app.post('/management/api/install')
    async def install_runtime(request: Request):
        auth(request); body = await request.json()
        if worker.active: raise HTTPException(409, '请先等待当前任务结束')
        from .installer import plan, install
        try: spec = plan(body.get('kind'), body.get('directory', ''), body.get('python', ''))
        except ValueError as e: raise HTTPException(400, str(e))
        if body.get('execute') is not True: return spec
        folder=worker.root/'logs';folder.mkdir(exist_ok=True)
        return launch(body['kind']+':install', lambda: install(body['kind'],body['directory'],body['python'],folder/(body['kind']+'-install.log')))

    @app.get('/management/api/access')
    async def access(request: Request):
        auth(request); return {'workerId': worker.worker_id, 'name': worker.args.name, 'host': worker.args.host, 'port': worker.args.port, 'boundServerId': get_owner(), 'credential': credentials['credential']}

    @app.post('/management/api/access')
    async def change_access(request: Request):
        auth(request); b = await request.json()
        if worker.active: raise HTTPException(409, '请先等待或取消当前任务')
        if b.get('action') == 'unbind':
            await unbind()
        elif b.get('action') == 'rotate':
            await unbind(); credentials['credential'] = secrets.token_urlsafe(32)
            path = worker.root / 'identity.json'; path.write_text(json.dumps(credentials)); path.chmod(0o600)
        elif b.get('action') == 'configure':
            if not isinstance(b.get('name'), str) or not 1 <= len(b['name']) <= 100 or not isinstance(b.get('host'), str) or not isinstance(b.get('port'), int) or not 1 <= b['port'] <= 65535: raise HTTPException(400, '名称或监听配置无效')
            path = worker.root / 'config' / 'service.json'; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: b[k] for k in ('name','host','port')})); worker.args.name = b['name']
        else: raise HTTPException(400, '未知操作')
        return {'ok': True, 'restartRequired': b.get('action') == 'configure'}

    @app.get('/management/api/diagnostics')
    async def diagnostics(request: Request):
        auth(request)
        logs = {}
        for kind in ('image','speech','video'):
            path = worker.root / 'logs' / (kind + '.jsonl')
            if path.exists(): logs[kind] = path.read_text('utf-8')[-16000:]
        return redact({'workerId': worker.worker_id, 'executors': worker.manager.snapshot(), 'logs': logs, 'resourceQuarantined': bool(worker.quarantined_lease)})

    static = Path(__file__).parent / 'web'
    if static.exists():
        app.mount('/management/static', StaticFiles(directory=static), name='management-static')
        @app.get('/management')
        async def page(): return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-store'})
