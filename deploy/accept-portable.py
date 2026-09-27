"""Explicit CPU-only Linux release acceptance; installs only Worker core wheels."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zhilume_worker.releases import Releases


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--wheel',required=True);parser.add_argument('--previous-wheel',required=True)
    parser.add_argument('--python',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args()
    report={'gpuInference':False,'modelsDownloaded':False,'tests':[]}
    with tempfile.TemporaryDirectory(prefix='zhilume-release-accept-') as tmp:
        release=Releases(Path(tmp)/'program',Path(tmp)/'data')
        old=release.install(args.previous_wheel,args.python)['version'];release.activate(old)
        executable=release.program/'current/bin/zhilume-worker'
        subprocess.run([str(executable),'--state',str(release.state),'--show-token'],check=True,capture_output=True)
        subprocess.run([str(executable),'--state',str(release.state),'--show-management-token'],check=True,capture_output=True)
        before={str(p.relative_to(release.state)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [release.state/'identity.json',release.state/'credentials/management-token']}
        new=release.install(args.wheel,args.python)['version'];release.activate(new)
        report['tests'].append('clean-wheel-install-and-upgrade')
        python=release.program/'current/bin/python'
        subprocess.run([str(python),'-c',"import importlib.util; assert importlib.util.find_spec('torch') is None; assert importlib.util.find_spec('PIL') is None"],check=True)
        report['tests'].append('core-without-torch-pillow-node')
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env={**os.environ,'ZHILUME_RESOURCE_LOCK_DIR':str(Path(tmp)/'gpu-locks')}
        with (Path(tmp)/'service.log').open('wb') as log:
            process=subprocess.Popen([str(executable),'--state',str(release.state),'--port',str(port)],stdout=log,stderr=log,env=env)
            try:
                token=(release.state/'credentials/management-token').read_text().strip()
                for _ in range(100):
                    try:
                        req=urllib.request.Request(f'http://127.0.0.1:{port}/management/api/overview',headers={'Authorization':'Bearer '+token})
                        with urllib.request.urlopen(req,timeout=2) as response: info=json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None:raise RuntimeError('Worker failed to start')
                        time.sleep(.1)
                else:raise RuntimeError('Worker did not become ready')
                assert info['version']==new and info['serviceOnline'] and not info['bound']
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/management') as page:assert b'<html' in page.read()
                try:release.rollback()
                except ValueError:pass
                else:raise AssertionError('Live service must reject activation')
                report['tests'].append('unbound-management-static-page-and-live-upgrade-guard')
            finally:
                process.terminate()
                try:process.wait(timeout=20)
                except subprocess.TimeoutExpired:process.kill();process.wait();raise
        assert release.rollback()['current']==old
        release.activate(new)
        after={p:hashlib.sha256((release.state/p).read_bytes()).hexdigest() for p in before}
        assert before==after
        report['tests'].append('rollback-and-identity-preservation')
    Path(args.report).write_text(json.dumps(report,indent=2)+'\n','utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
