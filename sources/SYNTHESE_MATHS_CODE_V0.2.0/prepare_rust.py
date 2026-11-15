"""Fetch hash-pinned official components into this workspace, without an installer."""
from pathlib import Path
import hashlib
import json
import shutil
import tarfile
import urllib.request
from urllib.parse import urlparse

BASE=Path(__file__).resolve().parent/'toolchains/rust'

def prepare():
    plan=json.loads((BASE/'download-plan.json').read_text(encoding='utf-8'))
    target=BASE/'install'
    target.mkdir(exist_ok=False)
    rows=[]
    for component in plan['components']:
        if component.get('name') not in {'rustc','rust-std','rust-mingw'}:raise ValueError('Unapproved component name')
        url=component['url'];parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.netloc!='static.rust-lang.org':raise ValueError('Unapproved distribution host')
        archive=BASE/Path(parsed.path).name
        count=0;digest=hashlib.sha256()
        with urllib.request.urlopen(url,timeout=45) as source,archive.open('xb') as output:
            while block:=source.read(1024*1024):
                count+=len(block)
                if count>250*1024*1024:raise ValueError('Component exceeds declared download ceiling')
                digest.update(block);output.write(block)
        if digest.hexdigest()!=component['sha256']:raise ValueError('Archive SHA256 mismatch')
        unpack=BASE/'unpacked'/component['name'];unpack.mkdir(parents=True,exist_ok=False)
        with tarfile.open(archive,'r:xz') as package:
            members=package.getmembers()
            if len(members)>100000 or sum(m.size for m in members)>2*1024**3:raise ValueError('Expanded archive exceeds bounds')
            for member in members:
                destination=(unpack/member.name).resolve()
                if not destination.is_relative_to(unpack.resolve()) or not (member.isfile() or member.isdir()):
                    raise ValueError('Non-file/directory or escaping archive member')
            package.extractall(unpack,members=members,filter='data')
        roots=list(unpack.iterdir())
        if len(roots)!=1:raise ValueError('Unexpected component archive layout')
        package_root=roots[0]
        for name in (package_root/'components').read_text(encoding='utf-8').splitlines():
            folder=(package_root/name).resolve()
            if not folder.is_relative_to(package_root.resolve()) or not folder.is_dir():raise ValueError('Invalid component root')
            for file in folder.rglob('*'):
                if not file.is_file() or file.name=='manifest.in':continue
                relative=file.relative_to(folder);destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists() and destination.read_bytes()!=file.read_bytes():raise ValueError('Component collision')
                shutil.copy2(file,destination)
        rows.append(dict(component,bytes=count))
        print(json.dumps({'downloaded':component['name'],'bytes':count,'sha256_verified':True}),flush=True)
    (BASE/'prepared.json').write_text(json.dumps({'status':'EXTRACTED_NOT_QUALIFIED','plan':plan,'components':rows,'install':str(target)},indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':prepare()
