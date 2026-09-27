"""Owned local media/inference subprocesses with bounded cancellation."""
import asyncio
import os
import signal
import subprocess

async def run_child(command, cwd, log, timeout=900, env=None):
    # No shell and no renderer-controlled command. Kill the owned process before acknowledging cancellation.
    with log.open('ab') as stream:
        process = await asyncio.create_subprocess_exec(*command, cwd=cwd, stdout=stream, stderr=stream,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', **(env or {})},
            start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            code = await asyncio.wait_for(process.wait(), timeout)
            if code:
                raise ValueError(f'子进程执行失败，请检查 Worker 本地 {log.name}；未生成可归档结果')
        except BaseException:
            if process.returncode is None:
                if os.name == 'nt':
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            raise

