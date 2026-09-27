"""Explicit Linux runtime installation. Never downloads weights or starts inference."""
import argparse
import asyncio
import json
import os
import platform
import sys
from pathlib import Path
from .processes import run_child

COMFY_REVISION = '79be670e2d9be63e238785af307369d2b9039ed1'
SPEECH_REVISION = 'ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c'


def plan(kind, directory, python):
    if kind not in ('image', 'video', 'speech'): raise ValueError('未知执行器')
    root=Path(directory)
    if not root.is_absolute() or root.exists(): raise ValueError('安装目录须为尚不存在的绝对路径；升级使用新目录，不覆盖旧环境')
    if not Path(python).is_absolute() or not Path(python).is_file(): raise ValueError('请指定已有 Python 的绝对路径')
    repository='https://github.com/index-tts/index-tts.git' if kind=='speech' else 'https://github.com/Comfy-Org/ComfyUI.git'
    revision=SPEECH_REVISION if kind=='speech' else COMFY_REVISION
    source=root/'source';env=root/'venv'
    commands=[['git','init',str(source)],['git','-C',str(source),'remote','add','origin',repository],['git','-C',str(source),'fetch','--depth','1','origin',revision],['git','-C',str(source),'checkout','--detach','FETCH_HEAD']]
    if kind=='speech': commands += [['uv','sync','--frozen','--no-dev','--python',python]]
    else: commands += [[python,'-m','venv',str(env)],[str(env/'bin/python'),'-m','pip','install','-r',str(source/'requirements.txt')]]
    return {'kind':kind,'directory':str(root),'source':str(source),'revision':revision,'commands':commands,'downloadsWeights':False,'startsInference':False}


async def install(kind, directory, python, log):
    if platform.system()!='Linux': raise ValueError('标准推理安装暂支持 Linux / WSL2；Windows 原生尚未验收')
    spec=plan(kind,directory,python);root=Path(directory);root.mkdir(parents=True,exist_ok=False)
    marker=root/'installation.json';marker.write_text(json.dumps({**spec,'state':'installing'},indent=2))
    try:
        for command in spec['commands']:
            cwd=Path(spec['source']) if command[0]=='uv' else root
            await run_child(command,cwd,log,timeout=3600,env={'GIT_LFS_SKIP_SMUDGE':'1','UV_PROJECT_ENVIRONMENT':str(root/'venv'),'HF_HUB_OFFLINE':'1'})
        marker.write_text(json.dumps({**spec,'state':'installed-unchecked'},indent=2))
        return {'state':'installed-unchecked','directory':str(root),'next':'配置模型路径并显式检查；未启动推理'}
    except BaseException:
        marker.write_text(json.dumps({**spec,'state':'incomplete'},indent=2));raise


def main():
    p=argparse.ArgumentParser(description='独立执行器安装；默认仅输出计划')
    p.add_argument('--executor',required=True,choices=['image','video','speech']);p.add_argument('--directory',required=True);p.add_argument('--python',required=True);p.add_argument('--execute',action='store_true');p.add_argument('--log',default='executor-install.log')
    a=p.parse_args();spec=plan(a.executor,a.directory,a.python)
    if a.execute: print(json.dumps(asyncio.run(install(a.executor,a.directory,a.python,Path(a.log).resolve())),ensure_ascii=False))
    else: print(json.dumps(spec,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
