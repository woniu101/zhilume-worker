"""Build an allowlisted Linux deployment bundle; never includes runtime state."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--wheel',required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];wheel=Path(args.wheel).resolve()
    if wheel.parent != root/'dist' or not wheel.name.startswith('zhilume_worker-') or wheel.suffix!='.whl':
        parser.error('请选择 dist 内的 Worker wheel')
    version=wheel.name.split('-')[1]
    paths=['deploy/language.md','config/language.example.json','deploy/install.sh','deploy/portable.md','deploy/standard-install.md','deploy/Dockerfile','deploy/compose.yaml',
           'deploy/runtime.linux.example.json','deploy/model-paths.linux.example.yaml',
           'src/zhilume_worker/releases.py','.dockerignore']
    paths += ['config/comfy.example.json','config/video.example.json','config/indextts.example.json']
    paths += ['dist/'+wheel.name]
    out=root/'dist'/f'zhilume-worker-{version}-linux-bundle.tar.gz'
    with tarfile.open(out,'w:gz') as archive:
        for name in paths:
            # Normalize metadata; no identities, logs, caches, credentials or media.
            file=root/name;info=archive.gettarinfo(str(file),arcname=name)
            info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o755 if name.endswith('.sh') else 0o644
            with file.open('rb') as stream:archive.addfile(info,stream)
    checksum=hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix+'.sha256').write_text(checksum+'  '+out.name+'\n','utf-8')
    print(json.dumps({'path':str(out),'sha256':checksum,'files':paths},ensure_ascii=False))


if __name__=='__main__':main()
