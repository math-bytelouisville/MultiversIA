"""Build and run the owned ASP.NET Core MVC studio with a workspace-only SDK."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,queue,re,shutil,subprocess,threading,time,urllib.request,uuid
from itertools import chain
BASE=Path(__file__).resolve().parent
SDK_ROOT=BASE/'toolchains/dotnet/install'
SDK_MANIFEST=BASE/'toolchains/dotnet/manifest.json'
SOURCE=BASE/'qcm_studio'
PROJECT_OUTPUTS=frozenset({'.vs','bin','obj'})
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def inventory(root,ignore_top=()):
    root=Path(root).resolve();files={}
    for entry in root.iterdir():
        if entry.name.casefold() in ignore_top:continue
        for p in chain((entry,), entry.rglob('*') if entry.is_dir() else ()):
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) or not p.resolve().is_relative_to(root):raise ValueError('Linked/escaping file refused')
            if p.is_file():files[p.relative_to(root).as_posix()]=sha(p)
    return files

def ignore_project_outputs(directory, names):
    return [name for name in names if name.casefold() in PROJECT_OUTPUTS] if Path(directory)==SOURCE else []
def dotnet_toolchain():
    m=json.loads(SDK_MANIFEST.read_bytes())
    if m.get('schema_version')!='mia-dotnet-toolchain-1' or m.get('status')!='SDK_INFO_VERIFIED':raise ValueError('SDK not qualified')
    if Path(m['install']).resolve()!=SDK_ROOT.resolve() or Path(m['dotnet']).resolve()!=(SDK_ROOT/'dotnet.exe').resolve():raise ValueError('Unexpected SDK location')
    if inventory(SDK_ROOT)!=m['files_sha256']:raise ValueError('SDK inventory changed')
    return {'path':str(SDK_ROOT/'dotnet.exe'),'root':str(SDK_ROOT),'version':m['sdk_version'],'manifest_sha256':sha(SDK_MANIFEST),'host_sha256':sha(SDK_ROOT/'dotnet.exe')}
def environment():
    env=dict(os.environ)
    for k in ('DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','MSBuildSDKsPath','ASPNETCORE_URLS','ASPNETCORE_HOSTINGSTARTUPASSEMBLIES','ASPNETCORE_ENVIRONMENT','DOTNET_ENVIRONMENT'):
        env.pop(k,None)
    cache=BASE/'toolchains/dotnet/cache';cache.mkdir(parents=True,exist_ok=True)
    for sub in ('appdata','localappdata','packages','http','plugins'):(cache/sub).mkdir(exist_ok=True)
    env.update(DOTNET_ROOT=str(SDK_ROOT),DOTNET_CLI_HOME=str(BASE/'toolchains/dotnet/home'),DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false',DOTNET_GENERATE_ASPNET_CERTIFICATE='false',DOTNET_NOLOGO='1',DOTNET_CLI_USE_MSBUILD_SERVER='0',MSBUILDDISABLENODEREUSE='1',ASPNETCORE_ENVIRONMENT='Production',APPDATA=str(cache/'appdata'),LOCALAPPDATA=str(cache/'localappdata'),NUGET_PACKAGES=str(cache/'packages'),NUGET_HTTP_CACHE_PATH=str(cache/'http'),NUGET_PLUGINS_CACHE_PATH=str(cache/'plugins'))
    return env
def build_studio():
    folder=BASE/'runtime'/('MVC-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    r={'status':'INCONCLUSIVE','folder':str(folder),'at':datetime.now(timezone.utc).isoformat(),'source_directory':str(SOURCE),'scope':'Owned local ASP.NET Core MVC project, not generic source migration'}
    try:
        before=inventory(SOURCE,PROJECT_OUTPUTS)
        if not before:raise ValueError('Source is empty')
        tool=dotnet_toolchain();r.update(source_files_sha256=before,toolchain=tool)
        project=folder/'source';shutil.copytree(SOURCE,project,ignore=ignore_project_outputs)
        if inventory(project)!=before:raise ValueError('Source copy differs')
        config=folder/'NuGet.Config';config.write_text('<configuration><packageSources><clear /></packageSources></configuration>',encoding='utf-8')
        output=folder/'published';output.mkdir()
        command=[tool['path'],'publish',str(project/'QcmDemo.csproj'),'-c','Release','-o',str(output),'--configfile',str(config),'--disable-build-servers','--nologo','-p:NuGetAudit=false','-p:UseSharedCompilation=false']
        r['command']=command
        p=subprocess.run(command,cwd=folder,env=environment(),capture_output=True,encoding='utf-8',errors='replace',timeout=120,creationflags=FLAGS)
        save(folder/'compilation.json',{'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        if p.returncode:raise RuntimeError('MVC compilation failed; see '+str(folder/'compilation.json'))
        if inventory(SOURCE,PROJECT_OUTPUTS)!=before or dotnet_toolchain()!=tool:raise RuntimeError('Source or SDK changed during build')
        assembly=output/'QcmDemo.dll'
        if not assembly.is_file():raise RuntimeError('MVC assembly absent')
        r.update(status='COMPILED',published=str(output),assembly=str(assembly),published_files_sha256=inventory(output))
    except Exception as exc:r.update(reason=type(exc).__name__+': '+str(exc))
    save(folder/'build.json',r)
    return r
def check_build(build):
    if build.get('status')!='COMPILED':raise ValueError('Build not compiled: '+build.get('reason',''))
    published=Path(build['published']).resolve()
    if not published.is_relative_to((BASE/'runtime').resolve()):raise ValueError('Build outside owned runtime')
    if Path(build['assembly']).resolve()!=published/'QcmDemo.dll':raise ValueError('Unexpected assembly')
    if inventory(published)!=build['published_files_sha256']:raise ValueError('Published build changed')
    if dotnet_toolchain()!=build['toolchain']:raise ValueError('SDK changed since build')

class StudioStorageBusy(RuntimeError):
    def __init__(self, log_path):
        self.log_path = log_path
        super().__init__(
            "Une autre instance de Game Editor utilise les sauvegardes. "
            "Ferme la fenêtre MultiversIA — Game Editor déjà ouverte, "
            "puis relance le raccourci."
        )


class StudioProcess:
    def __init__(self,build,data_dir=None):
        self.build=build;self.data_dir=Path(data_dir or BASE/'local/qcm-studio').resolve();self.process=None;self.url=None;self.logs=[]
        self.stable_origin = self.data_dir == (BASE/'local/qcm-studio').resolve()
        self.session_dir=Path(build['folder'])/('session-'+uuid.uuid4().hex);self.stopped=False
        if not self.data_dir.is_relative_to(BASE.resolve()):raise ValueError('Data must stay in this workspace')
    def start(self):
        if self.process is not None:raise RuntimeError('Process object already used')
        check_build(self.build);self.data_dir.mkdir(parents=True,exist_ok=True);self.session_dir.mkdir()
        env=environment();env['MIA_QCM_DATA_DIR']=str(self.data_dir)
        # The user's browser storage belongs to an origin including its port.
        # Disposable test stores retain ephemeral ports; the real studio does not.
        env.pop('MIA_QCM_PORT', None)
        if self.stable_origin: env['MIA_QCM_PORT']='5187'
        ready=queue.Queue();b=self.build
        self.process=subprocess.Popen([b['toolchain']['path'],b['assembly']],cwd=b['published'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',creationflags=FLAGS)
        def reader():
            with (self.session_dir/'server.log').open('w',encoding='utf-8',newline='') as log:
                for line in self.process.stdout:
                    log.write(line);log.flush()
                    self.logs.append(line)
                    if len(self.logs)>2000:self.logs.pop(0)
                    if re.fullmatch(r'READY [0-9]+\s*',line):ready.put(line.strip())
            ready.put(None)
        self.reader=threading.Thread(target=reader,daemon=True);self.reader.start()
        try:
            line=ready.get(timeout=25)
            if line is None:
                log=''.join(self.logs)
                if 'STORAGE_LOCKED: another Studio owns this data directory.' in log:
                    raise StudioStorageBusy(self.session_dir/'server.log')
                if self.stable_origin and ('AddressInUseException' in log or 'address already in use' in log.lower()):
                    raise RuntimeError('Le port local 5187 est déjà utilisé. Ferme l’autre instance du Game Editor ou libère ce port, puis réessaie. Aucune autre adresse n’a été choisie afin de conserver tes préférences et brouillons du navigateur.')
                raise RuntimeError('Server stopped before readiness: '+log.split('\n',1)[0]+'\nJournal complet : '+str(self.session_dir/'server.log')+'\n'+log[-800:])
            port=int(line.split()[1])
            if not 1<=port<=65535:raise RuntimeError('Invalid port')
            self.url='http://127.0.0.1:'+str(port)
            with urllib.request.urlopen(self.url+'/api/studio',timeout=5) as response:
                if response.status!=200:raise RuntimeError('Readiness failed')
            return self
        except Exception:self.stop();raise
    def stop(self):
        if self.process and not self.stopped:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            if hasattr(self,'reader'):self.reader.join(timeout=2)
            if self.process.stdout:self.process.stdout.close()
            save(self.session_dir/'server.json',{'exit_code':self.process.returncode,'pid':self.process.pid,'url':self.url,'log_file':str(self.session_dir/'server.log'),'data_directory':str(self.data_dir)})
            self.stopped=True
    def __enter__(self):return self.start()
    def __exit__(self,*args):self.stop()

def load_or_build():
    receipt=BASE/'local/studio-build.json'
    if receipt.is_file():
        try:
            b=json.loads(receipt.read_bytes())
            if not isinstance(b,dict):raise ValueError('Build receipt must be an object')
            if inventory(SOURCE,PROJECT_OUTPUTS)==b.get('source_files_sha256'):check_build(b);return b
        except (ValueError,KeyError,TypeError,OSError):pass
    b=build_studio()
    if b['status']!='COMPILED':raise RuntimeError(b.get('reason','Build failed'))
    save(receipt,b);return b

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');parser.add_argument('--data-dir');args=parser.parse_args()
    b=load_or_build();print(json.dumps({'status':b['status'],'build':str(Path(b['folder'])/'build.json')}),flush=True)
    if args.serve:
        control=Path(b['folder'])/('serve-'+uuid.uuid4().hex);control.mkdir()
        with StudioProcess(b,args.data_dir) as server:
            save(control/'server.json',{'url':server.url,'pid':server.process.pid,'stop_file':str(control/'stop.request')})
            print(json.dumps({'url':server.url,'control':str(control)}),flush=True)
            until=time.monotonic()+3600
            try:
                while time.monotonic()<until and not (control/'stop.request').exists() and server.process.poll() is None:time.sleep(.3)
            except KeyboardInterrupt:pass
