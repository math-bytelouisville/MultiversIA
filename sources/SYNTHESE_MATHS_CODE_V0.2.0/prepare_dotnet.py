"""Workspace-only SDK bootstrap from Microsoft's release metadata and SHA512."""
from pathlib import Path
import hashlib,json,os,subprocess,urllib.request,zipfile
from urllib.parse import urlparse
BASE=Path(__file__).resolve().parent/'toolchains/dotnet'
def prepare():
    meta=json.loads((BASE/'releases.json').read_bytes())
    sdk=meta['releases'][0]['sdk']
    package=next(f for f in sdk['files'] if f['rid']=='win-x64' and f['url'].endswith('.zip'))
    url=urlparse(package['url'])
    if url.scheme!='https' or url.hostname!='builds.dotnet.microsoft.com':raise ValueError('Distribution host refused')
    archive=BASE/Path(url.path).name
    if not archive.exists():
        size=0
        with urllib.request.urlopen(package['url'],timeout=60) as response,archive.open('xb') as output:
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>400*1024*1024:raise ValueError('Archive exceeds 400 MiB')
                output.write(block)
    actual=hashlib.file_digest(archive.open('rb'),'sha512').hexdigest()
    if actual!=package['hash']:raise ValueError('SDK SHA512 mismatch')
    print(json.dumps({'downloaded':str(archive),'sha512_verified':True}),flush=True)
    target=BASE/'install';target.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        if sum(x.file_size for x in z.infolist())>2*1024**3:raise ValueError('Expanded size exceeds 2 GiB')
        for item in z.infolist():
            path=(target/item.filename).resolve()
            if not path.is_relative_to(target.resolve()) or ':' in item.filename or ((item.external_attr>>16)&0o170000)==0o120000:raise ValueError('Archive path refused')
        z.extractall(target)
    dotnet=target/'dotnet.exe'
    env=dict(os.environ,DOTNET_ROOT=str(target),DOTNET_CLI_HOME=str(BASE/'home'),DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_GENERATE_ASPNET_CERTIFICATE='false',DOTNET_NOLOGO='1')
    response=subprocess.run([str(dotnet),'--info'],capture_output=True,text=True,timeout=30,env=env)
    files={str(p.relative_to(target)).replace('\\','/'):hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in target.rglob('*') if p.is_file()}
    manifest={'schema_version':'mia-dotnet-toolchain-1','status':'SDK_INFO_VERIFIED' if response.returncode==0 else 'INCONCLUSIVE','sdk_version':sdk['version'],'source':package,'archive':str(archive),'dotnet':str(dotnet),'install':str(target),'files_sha256':files,'info':{'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr},'scope':'SDK identity and --info only; MVC qualification is separate'}
    (BASE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':manifest['status'],'version':sdk['version'],'files':len(files)}),flush=True)
    if response.returncode:raise RuntimeError('dotnet --info failed')
if __name__=='__main__':prepare()
