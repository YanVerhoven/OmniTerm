// JavaScript virtual machine for OmniTerm. It never executes host commands or
// reads/writes user files other than its own session-state JSON document.
const fs = require('node:fs');
const path = require('node:path');
const posix = require('node:path').posix;
const crypto = require('node:crypto');
const os = require('node:os');

const now = () => new Date().toISOString();
const id = () => crypto.randomUUID();
const tokenize = source => {
  const result = []; let part = '', quote = '', escaped = false;
  for (let i=0;i<source.length;i++) {
    const ch=source[i];
    if (escaped) { part += ch; escaped = false; }
    else if (ch === '\\' && quote !== "'" && /[\s"'\\|&;<>]/.test(source[i+1]||'')) escaped = true;
    else if (quote) { if (ch === quote) quote = ''; else part += ch; }
    else if (ch === '"' || ch === "'") quote = ch;
    else if (/\s/.test(ch)) { if (part) { result.push(part); part = ''; } }
    else part += ch;
  }
  if (escaped) part += '\\';
  if (part) result.push(part);
  return result;
};
function splitOutside(source, delimiters) {
  const output=[];let start=0,quote='',escape=false,before=null;
  for(let i=0;i<source.length;i++){
    const ch=source[i];
    if(escape){escape=false;continue;}
    if(ch==='\\'&&quote!=="'"){escape=true;continue;}
    if(quote){if(ch===quote)quote='';continue;}
    if(ch==='"'||ch==="'"){quote=ch;continue;}
    const delimiter=delimiters.find(value=>source.startsWith(value,i));
    if(delimiter){output.push({text:source.slice(start,i).trim(),operator:before});i+=delimiter.length-1;start=i+1;before=delimiter;}
  }
  output.push({text:source.slice(start).trim(),operator:before});return output;
}
const textResult = (text, style = 'out') => ({ text: String(text), style });

function newState(shell) {
  const stamp = now(), fsTree = {};
  const fsKey = p => shell==='bash'?p:(p.length===3&&p.endsWith(':/')?p.slice(0,2):p.toLowerCase());
  const s = {
    kind: shell, cwd: shell === 'bash' ? '/root' : 'C:/Deploy', home: shell === 'bash' ? '/root' : 'C:/Users/admin',
    clock: stamp, lastExit: 0, history: [], fs: fsTree,
    env: { COMPUTERNAME: 'DESKTOP-LAB', OS: shell === 'bash' ? 'Ubuntu 24.04.1 LTS' : 'Windows_NT',
      USERNAME: shell === 'bash' ? 'root' : 'admin', HOME: shell === 'bash' ? '/root' : 'C:/Users/admin', PATH: shell === 'bash' ? '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' : 'C:/Windows/System32;C:/Windows;C:/Python312' },
    vars: {}, packages: {}, services: { ssh: 'running', cron: 'running', nginx: 'stopped', docker: 'stopped',
      BITS: 'running', EventLog: 'running', Spooler: 'running', wuauserv: 'stopped' },
    processes: { 4: 'System', 912: 'explorer', 1480: 'svchost', 2844: 'OmniTerm' },
    git: {}, networkLatency: 25, networkLoss: 0
  };
  const mkdir = p => { fsTree[fsKey(p)] = { name: posix.basename(p) || p, dir: true, data: '', executable: false, modified: stamp }; };
  const write = (p, data, executable = false) => { fsTree[fsKey(p)] = { name: posix.basename(p), dir: false, data, executable, modified: stamp }; };
  if (shell === 'bash') {
    ['/','/root','/etc','/usr','/usr/bin','/var','/var/log','/tmp','/home'].forEach(mkdir);
    write('/etc/os-release', 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n');
    write('/var/log/syslog', 'OmniTerm virtual session started.\nVirtual filesystem ready.\n');
    write('/root/check.sh', '#!/bin/bash\necho "virtual package check"\n', true);
    write('/root/deploy_linux.sh', '# Offline simulated deployment script\n', true);
  } else {
    ['C:','C:/Deploy','C:/Deploy/logs','C:/Deploy/tools','C:/Users','C:/Users/admin','C:/Users/admin/AppData','C:/Users/admin/AppData/Local','C:/Users/admin/AppData/Local/Temp','C:/Windows','C:/Windows/System32','C:/Program Files','C:/Program Files/WindowsApps'].forEach(mkdir);
    write('C:/Deploy/Deploy-Apps.ps1', '# Offline simulated deployment script\n');
  }
  return s;
}

class Simulator {
  constructor(file) {
    this.file = file;
    try { this.db = JSON.parse(fs.readFileSync(file, 'utf8')); }
    catch { this.db = { sessions: [], cleanup: [], settings: { theme: 'dark', fontSize: 14, speed: 1 } }; }
    if (!Array.isArray(this.db.sessions)) this.db.sessions = [];
    if (!Array.isArray(this.db.cleanup)) this.db.cleanup = [];
    if (!this.db.settings) this.db.settings = { theme: 'dark', fontSize: 14, speed: 1 };
    if (!this.db.sessions.length) this.importLegacy();
    const open = this.db.sessions.filter(s => s.open);
    if (!open.length) {
      const closed = this.db.sessions.sort((a,b) => b.updatedAt.localeCompare(a.updatedAt))[0];
      if (closed) { closed.open = true; this.db.activeId = closed.id; }
      else this.create('ps', 'temporary');
    }
    if (!this.db.sessions.some(s => s.id === this.db.activeId && s.open)) this.db.activeId = this.db.sessions.find(s => s.open)?.id;
    this.expire(); this.save();
  }
  importLegacy() {
    const legacyPath=path.join(os.homedir(),'.terminal_sim','sessions.sqlite3');
    if(!fs.existsSync(legacyPath))return;
    let database;
    try {
      const {DatabaseSync}=require('node:sqlite');database=new DatabaseSync(legacyPath,{readOnly:true});
      const rows=database.prepare('SELECT * FROM sessions ORDER BY tab_order, created_at').all();
      if(!rows.length)return;
      this.db.sessions=rows.map(row=>{
        const legacy=JSON.parse(row.state_json),shell=row.shell_profile==='bash'?'bash':'ps',state=newState(shell);
        state.cwd=legacy.cwd||state.cwd;state.home=legacy.home||state.home;state.clock=legacy.clock||now();
        state.history=legacy.history||[];state.env={...state.env,...legacy.env};state.vars=legacy.vars||{};
        state.fs={};for(const [filePath,file] of Object.entries(legacy.fs||{})){const key=shell==='bash'?filePath:filePath.toLowerCase().replace(/:\/$/i,':');state.fs[key]={name:file.n,dir:!!file.d,data:file.data||'',executable:!!file.x,modified:file.t||now()};}
        state.services=Object.fromEntries(Object.entries(legacy.services||{}).map(([name,value])=>[name,String(value.Status||value.status||'stopped').toLowerCase()]));
        state.processes=Object.fromEntries(Object.entries(legacy.processes||{}).map(([pid,value])=>[pid,typeof value==='string'?value:(value.Name||value.name||`process-${pid}`)]));
        state.networkLatency=legacy.network_latency_ms??25;state.networkLoss=legacy.network_loss_pct??0;
        const packageSources=[legacy.apt,legacy.winget,legacy.choco,legacy.pip];state.packages={};
        for(const source of packageSources||[])for(const [name,detail]of Object.entries(source||{}))state.packages[name]=typeof detail==='string'?detail:(detail.ver||detail.version||detail.new||'installed');
        for(const [root,repo]of Object.entries(legacy.git_repos||{}))state.git[root]=this.importGit(repo);
        return {id:row.id,name:row.name,shell,type:row.session_type||'temporary',pinned:!!row.is_pinned,
          open:!!row.is_open,createdAt:row.created_at||now(),updatedAt:row.last_activity_at||now(),
          expiresAt:row.auto_cleanup_enabled&&row.expires_at?row.expires_at:null,state,transcript:[]};
      });
      this.db.activeId=this.db.sessions.find(session=>session.open)?.id;
      try{this.db.cleanup=database.prepare('SELECT * FROM cleanup_logs ORDER BY id DESC LIMIT 100').all().map(row=>({id:String(row.id),sessionId:row.session_id,name:row.session_id,at:row.started_at,event:row.event,status:row.status,actions:JSON.parse(row.actions||'[]')}));}catch{}
    } catch(error) {
      this.db.sessions=[];
      this.db.cleanup.unshift({id:id(),sessionId:'__migration__',name:'Legacy session import',at:now(),event:'migration-error',status:'failed',actions:[String(error.message||error)]});
    } finally { if(database)database.close(); }
  }
  importGit(repo) {
    return {branch:repo.branch||'main',branches:repo.branches||[repo.branch||'main'],origin:repo.origin||'',
      staged:repo.staged||[],commits:(repo.commits||[]).map(commit=>typeof commit==='string'?{id:commit.split(' ')[0],message:commit.split(' ').slice(1).join(' ')}:commit)};
  }
  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temp = this.file + '.tmp';
    fs.writeFileSync(temp, JSON.stringify(this.db), 'utf8');
    fs.renameSync(temp, this.file);
  }
  create(shell = 'ps', type = 'temporary', name) {
    const created = now();
    const s = { id: id(), name: name || (shell === 'bash' ? 'Ubuntu' : 'PowerShell'), shell,
      type, pinned: false, open: true, createdAt: created, updatedAt: created,
      expiresAt: type === 'temporary' ? new Date(Date.now() + 12 * 3600000).toISOString() : null,
      state: newState(shell), transcript: [] };
    this.db.sessions.push(s); this.db.activeId = s.id; this.save(); return s;
  }
  current(idValue) { const s = this.db.sessions.find(x => x.id === idValue); if (!s) throw new Error('Session not found'); return s; }
  list() { return this.db.sessions.filter(x => x.open).sort((a,b) => Number(b.pinned)-Number(a.pinned) || a.createdAt.localeCompare(b.createdAt)).map(s => ({
    id:s.id,name:s.name,shell:s.shell,type:s.type,pinned:s.pinned,expires:s.expiresAt,createdAt:s.createdAt,active:s.id===this.db.activeId,cwd:s.state.cwd,transcript:s.transcript
  })); }
  expire() {
    const time = Date.now(); const expired = this.db.sessions.filter(s => s.type === 'temporary' && s.expiresAt && Date.parse(s.expiresAt) <= time);
    for (const s of expired) {
      this.db.sessions = this.db.sessions.filter(x => x.id !== s.id);
      this.db.cleanup.unshift({ id:id(), sessionId:s.id, name:s.name, at:now(), event:'expired-session-cleanup', status:'completed', actions:['Removed virtual session state.'] });
      this.db.cleanup=this.db.cleanup.slice(0,200);
    }
  }
  handle(op, data = {}) {
    let result;
    if (op === 'list') { this.expire(); if (!this.db.sessions.some(s => s.open)) this.create('ps'); result = this.list(); }
    else if (op === 'settings') result = this.db.settings;
    else if (op === 'saveSettings') { this.db.settings = { ...this.db.settings, ...data.settings }; result = this.db.settings; }
    else if (op === 'create') { result = { id:this.create(data.shell, data.type, data.name).id }; }
    else if (op === 'history') result = this.db.cleanup.slice(0, 100);
    else if (op === 'command') result = this.command(this.current(data.id), data.command || '');
    else {
      const s = this.current(data.id);
      if (op === 'rename') { const name = String(data.name || '').trim(); if (!name) throw new Error('Name cannot be empty'); s.name = name; }
      else if (op === 'activate') { this.db.activeId = s.id; }
      else if (op === 'close') { s.open = false; if (this.db.activeId === s.id) this.db.activeId = this.db.sessions.find(x => x.open)?.id; if (!this.db.sessions.some(x => x.open)) this.create('ps'); }
      else if (op === 'delete') { this.db.sessions = this.db.sessions.filter(x => x.id !== s.id); this.db.cleanup.unshift({ id:id(),sessionId:s.id,name:s.name,at:now(),event:'manual-delete',status:'completed',actions:['Removed virtual session state.'] }); if (this.db.activeId === s.id) this.db.activeId=this.db.sessions.find(x=>x.open)?.id; if (!this.db.sessions.some(x => x.open)) this.create('ps'); }
      else if (op === 'pin') { if (data.pinned && s.type !== 'permanent') throw new Error('Convert this session to permanent before pinning it.'); s.pinned = !!data.pinned; }
      else if (op === 'permanent') { s.type = 'permanent'; s.expiresAt = null; }
      else if (op === 'temporary') { s.type = 'temporary'; s.expiresAt = new Date(Date.now() + Number(data.hours || 12) * 3600000).toISOString(); }
      else if (op === 'extend') { if (s.type !== 'temporary') throw new Error('Permanent sessions do not expire.'); s.expiresAt = new Date(Math.max(Date.now(), Date.parse(s.expiresAt || now())) + Number(data.hours || 12) * 3600000).toISOString(); }
      else if (op === 'cleanup') { this.expire(); result = this.db.cleanup.slice(0,100); }
      else throw new Error(`Unknown operation: ${op}`);
      result ??= true;
    }
    this.save(); return result;
  }
  resolve(s, value) {
    let p = String(value || '.').replaceAll('\\','/');
    if (s.shell === 'bash') {
      if (p === '~') p=s.state.home; else if (p.startsWith('~/')) p=s.state.home+p.slice(1);
      return posix.normalize(p.startsWith('/') ? p : posix.join(s.state.cwd,p));
    }
    if (!/^[a-z]:/i.test(p)) p = s.state.cwd + '/' + p;
    p = posix.normalize(p).replace(/^([a-z]):/i, (_, drive) => drive.toUpperCase()+':');
    return p.length > 3 ? p.replace(/\/$/,'') : p;
  }
  key(s,p) { if (s.shell === 'bash') return p; const key=p.toLowerCase(); return key.length===3&&key.endsWith(':/')?key.slice(0,2):key; }
  node(s,p) {
    const resolved = this.resolve(s,p), key = this.key(s,resolved);
    if (resolved === '/' || (s.shell === 'ps' && /^[a-z]:\/?$/i.test(resolved))) return { name:resolved,dir:true,data:'' };
    return s.state.fs[key];
  }
  put(s,p,data,dir=false,executable=false) {
    const resolved=this.resolve(s,p), key=this.key(s,resolved), parent=this.node(s,posix.dirname(resolved));
    if (resolved !== '/' && !/^[a-z]:$/i.test(resolved) && (!parent || !parent.dir)) throw new Error(`No such directory: ${posix.dirname(resolved)}`);
    const existing=this.node(s,resolved);
    if (existing?.dir && !dir) throw new Error(`Is a directory: ${p}`);
    s.state.fs[key]={name:posix.basename(resolved)||resolved,dir,data:String(data),executable,modified:now()};
  }
  children(s,p) {
    const parent=this.resolve(s,p), parentKey=this.key(s,parent);
    return Object.entries(s.state.fs).filter(([k]) => k!==parentKey && this.key(s,posix.dirname(k))===parentKey)
      .map(([,n])=>n).sort((a,b)=>a.name.localeCompare(b.name,undefined,{sensitivity:'base'}));
  }
  remove(s,p,recursive=false) {
    const resolved=this.resolve(s,p), key=this.key(s,resolved), node=this.node(s,resolved);
    if (!node) throw new Error(`No such file or directory: ${p}`);
    if (node.dir) {
      const children=Object.keys(s.state.fs).filter(k=>k.startsWith(key.replace(/\/$/,'')+'/'));
      if (children.length && !recursive) throw new Error(`Directory not empty: ${p}`);
      children.forEach(k=>delete s.state.fs[k]);
    }
    delete s.state.fs[key];
  }
  command(session, source) {
    const s=session.state; source=String(source).trim(); if (!source) return {output:[],cwd:s.cwd};
    const commandPrompt=session.kind==='bash'?`root@vm:${s.cwd==='/root'?'~':s.cwd.startsWith('/root/')?'~'+s.cwd.slice(5):s.cwd}#`:`PS ${s.cwd.replaceAll('/','\\')}>`;
    session.transcript.push({type:'command',text:source,prompt:commandPrompt,at:now()}); s.history.push(source);
    if (s.history.length>500) s.history=s.history.slice(-500);
    let output=[];let previousCode=0;
    try {
      for (const chunk of splitOutside(source,['&&',';'])) {
        const segment=chunk.text;
        if (!segment) continue;
        if(chunk.operator==='&&'&&previousCode!==0)continue;
        if (segment==='clear'||segment==='cls'||segment==='clear-host') { session.transcript=[]; output=[];previousCode=0;continue; }
        if (segment==='exit'||segment==='logout') { output.push(textResult('Use the tab close button to close this virtual session.','dim'));previousCode=0;continue; }
        let pipeline=splitOutside(segment,['|']).map(part=>part.text).filter(Boolean); let pipeInput=null;
        for (let i=0;i<pipeline.length;i++) {
          const result=this.runCommand(session,pipeline[i],pipeInput); if(i===pipeline.length-1)output.push(...result.output); pipeInput=result.pipe ?? result.output.map(x=>x.text); s.lastExit=result.code;previousCode=result.code;
        }
      }
    } catch (error) { output.push(textResult(error.message,'err')); s.lastExit=1; }
    session.updatedAt=now(); session.transcript.push(...output.map(line=>({type:'output',...line,at:now()})));
    if (session.transcript.length>2500) session.transcript=session.transcript.slice(-2500);
    this.save(); return {output,cwd:s.cwd,exitCode:s.lastExit,transcript:session.transcript};
  }
  runCommand(session, source, pipeInput) {
    const sessionState=session.state, bash=session.shell==='bash';
    const assignment=source.match(/^\$([\w:]+)\s*=\s*(.*)$/);
    if(assignment){const value=assignment[2].replace(/^['"]|['"]$/g,'');if(assignment[1].toLowerCase().startsWith('env:'))sessionState.env[assignment[1].slice(4).toUpperCase()]=value;else sessionState.vars[assignment[1]]=value;return{output:[],code:0};}
    let redirect=null, append=false;
    const redir=source.match(/\s(>>|>)\s(.+)$/); if (redir) { redirect=redir[2].trim().replace(/^['"]|['"]$/g,''); append=redir[1]==='>>'; source=source.slice(0,redir.index).trim(); }
    const argv=tokenize(source); if (!argv.length) return {output:[],code:0};
    let name=argv.shift(), args=argv, cmd=bash?name:name.toLowerCase();
    const aliases={ls:'Get-ChildItem',dir:'Get-ChildItem',gci:'Get-ChildItem',cd:'Set-Location',pwd:'Get-Location',cat:'Get-Content',type:'Get-Content',echo:'Write-Output',rm:'Remove-Item',del:'Remove-Item',erase:'Remove-Item',rmdir:'Remove-Item',cp:'Copy-Item',copy:'Copy-Item',mv:'Move-Item',move:'Move-Item',mkdir:'New-Item',md:'New-Item',cls:'Clear-Host',where:'Where-Object',select:'Select-Object',sort:'Sort-Object',measure:'Measure-Object','%':'ForEach-Object'};
    if (!bash) cmd=(aliases[cmd]||name).toLowerCase();
    const out=[]; const emit=(v,style='out')=>out.push(textResult(v,style));
    const positional=args.filter(a=>!a.startsWith('-')&&(bash||!a.startsWith('/')));
    let pathArg=positional[0]||'.';
    let code=0, pipe=undefined;
    const scriptPath=['bash','sh','source'].includes(cmd)?positional[0]:name;
    if ((['bash','sh','source'].includes(cmd)||/\.(sh|ps1)$/i.test(name)||name.startsWith('./')||name.startsWith('.\\'))&&scriptPath) {
      const file=this.node(session,scriptPath);if(!file||file.dir)throw new Error(`Script not found: ${scriptPath}`);
      for(const scriptLine of file.data.split(/\r?\n|\s*;\s*/)){
        const statement=scriptLine.trim();if(!statement||statement.startsWith('#')||statement.startsWith('//'))continue;
        const result=this.runCommand(session,statement,null);out.push(...result.output);code=result.code;if(code!==0)break;
      }
    }
    else if(cmd==='sudo'){
      if(!args.length)emit('sudo: command is required; privilege changes do not affect the host.','warn');
      else if(['su','-i','-s'].includes(args[0])){emit('sudo: interactive privilege escalation is unavailable in the isolated simulator.','warn');code=126;}
      else {const result=this.runCommand(session,args.join(' '),pipeInput);out.push(...result.output);pipe=result.pipe;code=result.code;}
    }
    else if(cmd==='su'){emit('su: switching host users is unavailable in the isolated simulator.','warn');code=126;}
    else if(['curl','wget','ssh','scp','nmap','nc','netcat','traceroute','tracepath','dig','nslookup','host'].includes(cmd)){
      emit(`${name}: network access is disabled; this simulator does not contact external hosts.`,'warn');code=126;
    }
    else if(['mount','umount','chown','chgrp','fdisk','mkfs','fsck','iptables','ufw','systemctl-enable','crontab'].includes(cmd)){
      if(['mount','umount'].includes(cmd))emit(`${name}: mount state is virtual and cannot access host devices.`,'warn');
      else emit(`${name}: administrative changes to the host are blocked in the virtual environment.`,'warn');code=126;
    }
    else if(cmd==='chmod'){
      const target=positional.at(-1);const file=this.node(session,target);if(!file)throw new Error(`chmod: cannot access '${target}': No such file or directory`);file.executable=/[x57]/.test(args[0]||'');
    }
    else if(['help','man','get-help'].includes(cmd)) {
      ['Virtual commands: cd, pwd, ls/dir, mkdir, touch, cat, echo, cp, mv, rm, find, grep, sed, head, tail, wc, history, clear.',
       'System simulation: ps, systemctl, journalctl, df, du, free, uname, hostname, whoami, ping, apt, pip, winget, git, docker.',
       'All command effects stay inside this session’s virtual filesystem.'].forEach(v=>emit(v,'dim'));
    } else if (['pwd','get-location'].includes(cmd)) { emit(bash?s.cwd:s.cwd.replaceAll('/','\\')); pipe=[bash?s.cwd:s.cwd.replaceAll('/','\\')]; }
    else if (['cd','set-location','push-location','pop-location'].includes(cmd)) {
      if (cmd==='push-location') sessionState.locationStack=(sessionState.locationStack||[]).concat(s.cwd);
      if (cmd==='pop-location') { const stack=sessionState.locationStack||[]; if (!stack.length) throw new Error('Location stack is empty'); pathArg=stack.pop(); }
      const dest=this.resolve(session,pathArg), node=this.node(session,dest); if (!node?.dir) throw new Error(`Directory not found: ${pathArg}`); s.cwd=dest;
    }
    else if (['ls','get-childitem'].includes(cmd)) {
      const target=this.resolve(session,pathArg), node=this.node(session,target); if (!node) throw new Error(`Path not found: ${pathArg}`);
      const entries=node.dir?this.children(session,target):[node];
      const visible=entries.filter(n=>args.some(a=>a.toLowerCase()==='-force'||a.includes('a')&&a.startsWith('-'))||!n.name.startsWith('.'));
      visible.forEach(n=>emit((n.dir?'📁 ':'   ')+n.name+(n.dir?'/':''),n.dir?'info':'out'));
      if (!visible.length) emit('(empty)','dim'); pipe=bash?visible.map(n=>n.name):visible.map(n=>({Name:n.name,Mode:n.dir?'d-----':'-a----',Length:n.data.length}));
    }
    else if (['mkdir','new-item','touch'].includes(cmd)) {
      const names=positional.length?positional:[pathArg];
      for (const p of names) { const dest=this.resolve(session,p), isDir=cmd!=='touch'&&!args.some(x=>x.toLowerCase()==='-itemtype'&&args[args.indexOf(x)+1]?.toLowerCase()==='file');
        if (this.node(session,dest)) { if (isDir) throw new Error(`Already exists: ${p}`); continue; }
        if(isDir&&(args.includes('-p')||args.includes('--parents'))){const segments=dest.split('/').filter(Boolean);let cursor=dest.startsWith('/')?'/':segments.shift()+':';for(const segment of segments){cursor=cursor==='/'?`/${segment}`:`${cursor.replace(/\/$/,'')}/${segment}`;if(!this.node(session,cursor))this.put(session,cursor,'',true);}}
        else this.put(session,dest,'',isDir);
      }
    }
    else if (['echo','write-output','write-host','printf'].includes(cmd)) {
      let value=args.join(' ').replace(/\\n/g,'\n');value=value.replace(/\$env:([\w]+)/gi,(_,k)=>s.env[k.toUpperCase()]??'').replace(/\$\{?([\w]+)\}?/g,(_,k)=>s.vars[k]??s.env[k]??'');
      if(cmd==='printf'){for(let i=1;i<args.length;i++)value=value.replace(/%([sdif])/,(m,f)=>f==='f'?String(Number(args[i])):args[i]);}
      value.split('\n').forEach(v=>emit(v)); pipe=value.split('\n');
    }
    else if (['cat','get-content','type'].includes(cmd)) {
      const targets=positional.length?positional:(pipeInput?[]:[]); let lines=[];
      if (!targets.length && pipeInput) lines=pipeInput.map(String);
      else if (!targets.length) throw new Error('A path or pipeline input is required.');
      else for (const p of targets) { const n=this.node(session,p); if (!n||n.dir) throw new Error(`File not found: ${p}`); lines.push(...n.data.replace(/\n$/,'').split('\n')); }
      lines.forEach(v=>emit(v)); pipe=lines;
    }
    else if (['set-content','add-content','out-file'].includes(cmd)) {
      const target=positional.at(-1); if (!target) throw new Error('A path is required.');
      const value=(pipeInput||positional.slice(0,-1)).join('\n'); const old=this.node(session,target); this.put(session,target,(cmd==='add-content'||args.includes('-Append')?old?.data||'':'')+value+'\n');
    }
    else if(cmd==='clear-content'){for(const target of positional){const file=this.node(session,target);if(!file||file.dir)throw new Error(`File not found: ${target}`);file.data='';file.modified=now();}}
    else if(cmd==='rename-item'){
      if(positional.length<2)throw new Error('Rename-Item requires a path and a new name.');const [source,newName]=positional.slice(-2),from=this.resolve(session,source),node=this.node(session,from);if(!node)throw new Error(`Path not found: ${source}`);const dest=this.resolve(session,posix.join(posix.dirname(from),newName)),oldKey=this.key(session,from),newKey=this.key(session,dest);const entries=Object.entries(s.fs).filter(([key])=>key===oldKey||key.startsWith(oldKey.replace(/\/$/,'')+'/'));for(const [key,value] of entries){const moved=newKey+key.slice(oldKey.length);s.fs[moved]={...value,name:posix.basename(moved),modified:now()};delete s.fs[key];}
    }
    else if (['rm','remove-item','rmdir'].includes(cmd)) {
      for (const p of positional) this.remove(session,p,args.includes('-Recurse')||args.includes('-r')||args.includes('-rf')||args.includes('-Force')&&args.includes('-Recurse'));
    }
    else if (['cp','copy-item','copy'].includes(cmd)||['mv','move-item','move'].includes(cmd)) {
      if (positional.length<2) throw new Error('Expected a source and destination.');
      const [from,to]=positional.slice(-2), src=this.node(session,from); if (!src) throw new Error(`Source not found: ${from}`);
      const destNode=this.node(session,to), dest=this.resolve(session,destNode?.dir?posix.join(this.resolve(session,to),src.name):to);
      if (src.dir) { const descendants=Object.entries(s.fs).filter(([k])=>k===this.key(session,this.resolve(session,from))||k.startsWith(this.key(session,this.resolve(session,from)).replace(/\/$/,'')+'/')); for (const [k,n] of descendants) { const rel=k.slice(this.key(session,this.resolve(session,from)).length); s.fs[this.key(session,dest+rel)]={...n,name:posix.basename(dest+rel),modified:now()}; } }
      else this.put(session,dest,src.data,false,src.executable);
      if (['mv','move-item','move'].includes(cmd)) this.remove(session,from,true);
    }
    else if (['grep','select-string','findstr'].includes(cmd)) {
      const pattern=positional[0]||'', inputs=pipeInput||positional.slice(1).flatMap(p=>{const n=this.node(session,p); if (!n||n.dir) return []; return n.data.splitLines ? n.data.splitLines() : n.data.split(/\r?\n/);});
      const matched=inputs.filter(line=>args.includes('-i')?line.toLowerCase().includes(pattern.toLowerCase()):line.includes(pattern)); matched.forEach(v=>emit(v)); pipe=matched; code=matched.length?0:1;
    }
    else if (cmd==='find') {
      const root=this.resolve(session,positional[0]||'.'), pattern=args.includes('-name')?args[args.indexOf('-name')+1]:'*';
      const all=[[root,this.node(session,root)],...Object.entries(s.fs).filter(([k])=>k.startsWith(root.replace(/\/$/,'')+'/'))];
      const regex=new RegExp('^'+pattern.replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$');
      all.filter(([,n])=>n&&regex.test(n.name)).forEach(([k])=>emit(k)); pipe=out.map(v=>v.text);
    }
    else if (['head','tail','wc'].includes(cmd)) {
      const nlines=Number(args.find(x=>/^\d+$/.test(x))||10), lines=pipeInput||((this.node(session,pathArg)&&!this.node(session,pathArg).dir)?this.node(session,pathArg).data.split(/\r?\n/):[]);
      const result=cmd==='head'?lines.slice(0,nlines):cmd==='tail'?lines.slice(-nlines):[`${lines.length} ${lines.reduce((n,x)=>n+String(x).trim().split(/\s+/).filter(Boolean).length,0)} ${lines.join('\n').length}`];
      result.forEach(v=>emit(v)); pipe=result;
    }
    else if (cmd==='sed') {
      const script=args.find(x=>x.startsWith('s/')); let lines=pipeInput||[];
      if (!lines.length&&positional.length>1) {const n=this.node(session,positional.at(-1));if(!n||n.dir)throw new Error(`File not found: ${positional.at(-1)}`);lines=n.data.split(/\r?\n/);}
      if (!script) throw new Error('Supported form: sed s/old/new/g'); const [,old,repl,flags='']=script.split('/'); const regex=new RegExp(old.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),flags.includes('g')?'g':'');
      const result=lines.map(x=>String(x).replace(regex,repl)); if(args.includes('-i')) {if(!positional.length)throw new Error('Path required for -i');this.put(session,positional.at(-1),result.join('\n')+'\n');} else {result.forEach(v=>emit(v));pipe=result;}
    }
    else if (cmd==='awk') {const expression=args[0]||'', fields=[...expression.matchAll(/\$([0-9]+)/g)].map(x=>Number(x[1]));let lines=pipeInput||[];if(!lines.length&&positional.length>1){const file=this.node(session,positional.at(-1));if(!file||file.dir)throw new Error(`awk: cannot open ${positional.at(-1)}`);lines=file.data.split(/\r?\n/);}pipe=lines.map(value=>{const cols=String(value).split(/\s+/);return fields.map(n=>n===0?value:cols[n-1]||'').join(' ');});pipe.forEach(v=>emit(v));}
    else if (cmd==='xargs') {const action=args.filter(x=>!x.startsWith('-')).join(' ');if(!action)throw new Error('xargs: missing command');const inputs=pipeInput||[];for(const value of inputs){const expanded=action.replaceAll('{}',String(value));const result=this.runCommand(session,expanded,null);out.push(...result.output);code=result.code;}}
    else if (cmd==='tar') {
      const create=args.includes('-c')||args.some(x=>x.startsWith('-')&&x.includes('c')),list=args.includes('-t')||args.some(x=>x.startsWith('-')&&x.includes('t')),extract=args.includes('-x')||args.some(x=>x.startsWith('-')&&x.includes('x'));
      const archiveAt=args.findIndex(x=>x==='-f'||x==='--file'),archive=archiveAt>=0?args[archiveAt+1]:positional[0];if(!archive)throw new Error('tar: archive path is required');
      if(create){const members=positional.filter(p=>p!==archive).map(p=>{const resolved=this.resolve(session,p),node=this.node(session,p);if(!node)throw new Error(`tar: ${p}: Cannot stat: No such file or directory`);return{path:posix.relative(s.cwd,resolved),node:{...node}};});this.put(session,archive,'OMNITERM_TAR\n'+JSON.stringify(members));emit(`Created virtual archive ${archive}`,'ok');}
      else {const file=this.node(session,archive);if(!file||file.dir||!file.data.startsWith('OMNITERM_TAR\n'))throw new Error(`tar: ${archive}: Not a virtual archive`);const members=JSON.parse(file.data.slice('OMNITERM_TAR\n'.length));if(list)members.forEach(m=>emit(m.path));if(extract)for(const member of members){const dest=this.resolve(session,member.path);if(member.node.dir)this.put(session,dest,'',true);else this.put(session,dest,member.node.data,false,member.node.executable);emit(`Extracted ${member.path}`,'ok');}}
    }
    else if (['history','get-history'].includes(cmd)) { s.history.forEach((x,i)=>emit(`${String(i+1).padStart(4)}  ${x}`)); }
    else if (cmd==='true'){}
    else if (cmd==='false'){code=1;}
    else if (cmd==='seq') {let start=1,step=1,end=Number(args[0]);if(args.length===2)[start,end]=args.map(Number);if(args.length>=3)[start,step,end]=args.slice(0,3).map(Number);if(!Number.isFinite(end)||step===0)throw new Error('seq: invalid argument');pipe=[];for(let n=start;step>0?n<=end:n>=end;n+=step){emit(n);pipe.push(String(n));}}
    else if (['basename','dirname'].includes(cmd)) {const value=positional[0]||'';emit(cmd==='basename'?posix.basename(value):posix.dirname(value)||'.');}
    else if (cmd==='which'||cmd==='command') {const commands=new Set(['bash','sh','ls','cat','grep','find','sed','awk','tar','git','apt','pip','python','docker','systemctl']);const names=args.filter(x=>!x.startsWith('-'));for(const item of names){if(commands.has(item)){emit(`/usr/bin/${item}`);}else code=1;}}
    else if (cmd==='export') {for(const value of args){const at=value.indexOf('=');if(at>0){s.env[value.slice(0,at)]=value.slice(at+1);s.vars[value.slice(0,at)]=value.slice(at+1);}}}
    else if (cmd==='unset'||cmd==='remove-variable') {for(const key of args){delete s.env[key];delete s.vars[key];}}
    else if (cmd==='get-variable') {for(const key of positional){if(key in s.vars)emit(`${key} = ${s.vars[key]}`);else if(key in s.env)emit(`${key} = ${s.env[key]}`);}}
    else if (cmd==='set-variable') {const at=args.findIndex(x=>x.toLowerCase()==='-value'),nameAt=args.findIndex(x=>x.toLowerCase()==='-name'),key=nameAt>=0?args[nameAt+1]:args[0];if(at>=0&&key)s.vars[key]=args[at+1]||'';}
    else if (cmd==='get-alias') {Object.entries(aliases).forEach(([Name,Definition])=>emit(`${Name.padEnd(16)} ${Definition}`));}
    else if (['date','get-date'].includes(cmd)) { const value=new Date(s.clock).toString(); emit(value); pipe=[value]; }
    else if (['whoami','hostname','uname','ver','$psversiontable'].includes(cmd)) { const value=cmd==='whoami'?(bash?'root':'DESKTOP-LAB\\admin'):cmd==='hostname'?'DESKTOP-LAB':bash?'Linux vm 6.8.0-45-generic x86_64 GNU/Linux':'Microsoft Windows [Version 10.0.22631.1]'; emit(value); pipe=[value]; }
    else if (['env','printenv'].includes(cmd)) { Object.entries(s.env).sort().forEach(([k,v])=>emit(`${k}=${v}`)); }
    else if (cmd==='export') { args.forEach(pair=>{const i=pair.indexOf('=');if(i>0)s.env[pair.slice(0,i)]=pair.slice(i+1);}); }
    else if (cmd==='history-clear'||cmd==='clear-history') { s.history=[]; }
    else if (['ps','top','get-process','gps'].includes(cmd)) { const processes=Object.entries(s.processes).map(([pid,n])=>({Id:Number(pid),PID:Number(pid),Name:n,CPU:pid==='4'?12.5:1.2,State:'Running'})); emit(' PID  NAME                 STATE'); processes.forEach(p=>emit(`${String(p.PID).padStart(5)}  ${String(p.Name).padEnd(20)} ${p.State}`)); pipe=processes; }
    else if(cmd==='start-process'){const name=positional[0];if(!name)throw new Error('Start-Process requires a virtual program name.');const pid=String(Math.max(1000,...Object.keys(s.processes).map(Number))+1);s.processes[pid]=name.replace(/\.exe$/i,'');emit(`Started virtual process ${name} (PID ${pid}).`,'ok');}
    else if(cmd==='stop-process'){const pid=args.find(x=>/^\d+$/.test(x)),name=positional.find(x=>!/^\d+$/.test(x));const targets=Object.keys(s.processes).filter(key=>pid?key===pid:name?s.processes[key].toLowerCase()===name.toLowerCase():false);if(!targets.length)throw new Error('No simulated process matched the request.');targets.forEach(key=>delete s.processes[key]);emit('The simulated process was stopped.','ok');}
    else if(['kill','pkill'].includes(cmd)){const target=args.find(x=>/^\d+$/.test(x))||positional.at(-1);const matches=Object.keys(s.processes).filter(pid=>cmd==='kill'?pid===target:s.processes[pid].toLowerCase().includes(String(target||'').toLowerCase()));if(!matches.length)throw new Error(`No virtual process matched ${target||''}.`);matches.forEach(pid=>delete s.processes[pid]);}
    else if (['systemctl','service','get-service','start-service','stop-service','restart-service'].includes(cmd)) {
      const [action,service]=args; if (action==='list-units'||action==='--type=service'||cmd==='get-service') {const services=Object.entries(s.services).map(([Name,Status])=>({Name,Status:Status==='running'?'Running':'Stopped',DisplayName:Name}));services.forEach(p=>emit(`${p.Name.padEnd(18)} ${p.Status}`));pipe=services;}
      else { const serviceName=['start-service','stop-service','restart-service'].includes(cmd)?action:service;const serviceAction=cmd==='start-service'?'start':cmd==='stop-service'?'stop':cmd==='restart-service'?'restart':action;if(!s.services[serviceName])throw new Error(`Unit ${serviceName}.service could not be found.`); if(['start','stop','restart'].includes(serviceAction))s.services[serviceName]=serviceAction==='stop'?'stopped':'running'; emit(`${serviceName}.service - ${s.services[serviceName]}`); }
    }
    else if (cmd==='df') { emit('Filesystem      Size  Used Avail Use% Mounted on');const used=Object.values(s.fs).reduce((v,n)=>v+(n.data||'').length,0);emit(`virtualfs       10G   ${Math.ceil(used/1048576)}M    10G   1% /`); }
    else if (cmd==='du') { const n=this.node(session,pathArg);if(!n)throw new Error(`No such path: ${pathArg}`);const prefix=this.key(session,this.resolve(session,pathArg)).replace(/\/$/,'')+'/';const size=Object.entries(s.fs).filter(([k])=>k===this.key(session,this.resolve(session,pathArg))||k.startsWith(prefix)).reduce((v,[,x])=>v+(x.data||'').length,0);emit(`${Math.ceil(size/1024)}\t${pathArg}`); }
    else if (cmd==='free') { emit('              total        used        free      shared  buff/cache   available');emit('Mem:           7.7Gi       1.2Gi       5.1Gi        32Mi       1.4Gi       6.2Gi');emit('Swap:          2.0Gi          0B       2.0Gi'); }
    else if (cmd==='uptime') emit('up 2 days, 4:18, 1 user, load average: 0.08, 0.05, 0.02');
    else if(['get-netipaddress','ipconfig'].includes(cmd)){emit('Windows IP Configuration');emit('Ethernet adapter Virtual Ethernet:');emit('   IPv4 Address. . . . . . . . . . . : 192.0.2.10');emit('   Subnet Mask . . . . . . . . . . . : 255.255.255.0');emit('   Default Gateway . . . . . . . . . : 192.0.2.1');}
    else if(['get-nettcpconnection','netstat'].includes(cmd)){emit('Active Connections');emit('  Proto  Local Address          Foreign Address        State           PID');emit('  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       912');}
    else if(['get-volume','get-disk','fdisk','lsblk'].includes(cmd)){emit('Number FriendlyName       Size       HealthStatus');emit('0      Virtual Disk       64 GB      Healthy');emit('Device      Start       End   Sectors  Size Type');emit('/dev/vda1    2048 134217727 134215680   64G Linux filesystem');}
    else if(cmd==='test-netconnection'){emit('WARNING: Network access is disabled.','warn');emit('ComputerName     : '+(positional[0]||'virtual-host'));emit('RemoteAddress    : 192.0.2.1');emit('TcpTestSucceeded : False');code=1;}
    else if(['get-simulatednetwork','set-simulatednetwork'].includes(cmd)){if(cmd==='set-simulatednetwork'){const loss=args.find(x=>x.toLowerCase().startsWith('-loss'))?.split(':').at(-1);if(loss!==undefined)s.networkLoss=Math.max(0,Math.min(100,Number(loss)||0));}emit(`Virtual network: latency ${s.networkLatency} ms; packet loss ${s.networkLoss}%. External access disabled.`);}
    else if(['systeminfo','ver','systemd-analyze'].includes(cmd)){emit('Host Name:                 DESKTOP-LAB');emit('OS Name:                   OmniTerm Virtual Environment');emit('OS Version:                10.0.22631 N/A Build 22631');emit('System Type:               x64-based PC');}
    else if(['tasklist','taskkill'].includes(cmd)){if(cmd==='taskkill'){const pidAt=args.findIndex(x=>x.toLowerCase()==='/pid'),imageAt=args.findIndex(x=>x.toLowerCase()==='/im'),pid=pidAt>=0?args[pidAt+1]:null,image=imageAt>=0?args[imageAt+1]?.replace(/\.exe$/i,'').toLowerCase():null;const matches=Object.keys(s.processes).filter(key=>pid?key===pid:image?s.processes[key].toLowerCase()===image:false);if(!matches.length)throw new Error('No simulated process matched the request.');matches.forEach(key=>delete s.processes[key]);emit('SUCCESS: The simulated process was terminated.','ok');}else{emit('Image Name                     PID Session Name');Object.entries(s.processes).forEach(([pid,name])=>emit(`${name.padEnd(30)} ${pid} Console`));}}
    else if(['route','arp'].includes(cmd)){emit(cmd==='route'?'IPv4 Route Table':'Interface: 192.0.2.10 --- 0x7');emit(cmd==='route'?'Network Destination        Netmask          Gateway       Interface  Metric':'  Internet Address      Physical Address      Type');emit(cmd==='route'?'0.0.0.0                    0.0.0.0          192.0.2.1     192.0.2.10     25':'  192.0.2.1             00-00-5e-00-53-01     dynamic');}
    else if (['ping','test-connection'].includes(cmd)) { emit('Network access is disabled; virtual sample response only.','warn');emit('4 packets transmitted, 4 received, 0% packet loss'); }
    else if (['apt','apt-get','winget','pip','pip3','choco'].includes(cmd)) {
      const verb=args[0]||'list', pkg=args.find(x=>!x.startsWith('-')&&!['install','remove','uninstall','upgrade','update','list','search','show','info','--version'].includes(x));
      if(['install','upgrade','update','remove','uninstall'].includes(verb)) { if(pkg){if(['remove','uninstall'].includes(verb))delete s.packages[pkg];else s.packages[pkg]=({firefox:'144.0',requests:'2.32.3',numpy:'2.1.2','7zip':'24.08',git:'2.47.0'})[pkg]||'1.0.0';emit(`${verb}: ${pkg} completed in virtual environment.`,'ok');}else emit(`${verb}: package metadata refreshed (offline sample).`,'ok'); }
      else if(['list','--version','search'].includes(verb)){emit(verb==='--version'?'Package manager simulator 1.0':'NAME                 VERSION');Object.entries(s.packages).forEach(([k,v])=>emit(`${k.padEnd(20)} ${v}`));}else emit(`${cmd}: simulated package manager; network access is offline.`,'dim');
    }
    else if (cmd==='git') {
      const action=args[0]||'status'; if(action==='init'){const root=this.resolve(session,pathArg);s.git[root]={branch:'main',commits:[],staged:[]};emit(`Initialized empty virtual Git repository in ${root}/.git/`,'ok');}
      else if(action==='status'){const repo=Object.values(s.git)[0];if(!repo)throw new Error('fatal: not a git repository (or any parent directory)');emit(`On branch ${repo.branch}`);emit(repo.staged.length?'Changes to be committed:':'nothing to commit, working tree clean','dim');}
      else if(action==='branch'){const repo=Object.values(s.git)[0];if(!repo)throw new Error('fatal: not a git repository');emit(`* ${repo.branch}`);}
      else if(action==='log'){const repo=Object.values(s.git)[0];(repo?.commits||[]).slice().reverse().forEach(c=>emit(`commit ${c.id}\n    ${c.message}`));if(!repo)throw new Error('fatal: not a git repository');}
      else if(['add','commit'].includes(action)){const repo=Object.values(s.git)[0];if(!repo)throw new Error('fatal: not a git repository');if(action==='add'){repo.staged.push(...args.slice(1));emit('Changes staged in virtual index.','ok');}else{repo.commits.push({id:id().slice(0,8),message:args.slice(1).join(' ').replace(/^-m\s*/, '')||'virtual commit'});repo.staged=[];emit(`[${repo.branch}] ${repo.commits.at(-1).id} ${repo.commits.at(-1).message}`,'ok');}}
      else emit(`git: '${action}' is not supported in the virtual environment.`,'err');
    }
    else if (cmd==='docker') { emit('Docker virtual state: no containers are running. The host Docker engine is never started.','dim'); }
    else if (cmd==='journalctl') { const n=this.node(session,bash?'/var/log/syslog':'C:/Deploy/logs/system.log');(n?.data||'Virtual system journal is empty.').trimEnd().split('\n').forEach(v=>emit(v)); }
    else if (['where-object','select-object','sort-object','measure-object','format-table','format-list','out-string','out-null','tee-object','foreach-object','convertto-json','convertfrom-json'].includes(cmd)) {
      const values=pipeInput||[];
      if(cmd==='where-object') {
        const expression=args.join(' ');const prop=expression.match(/\$?_?\.?([\w]+)\s+-(eq|ne|gt|ge|lt|le|like|match)\s+['"]?([^'"}]+)['"]?/i);
        pipe=values.filter(value=>{if(!prop)return Boolean(value);const actual=value&&typeof value==='object'?value[prop[1]]:value,expected=prop[3].trim(),a=Number(actual),b=Number(expected);switch(prop[2].toLowerCase()){case'eq':return String(actual).toLowerCase()===expected.toLowerCase();case'ne':return String(actual).toLowerCase()!==expected.toLowerCase();case'gt':return a>b;case'ge':return a>=b;case'lt':return a<b;case'le':return a<=b;case'like':return new RegExp('^'+expected.replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$','i').test(String(actual));case'match':return String(actual).toLowerCase().includes(expected.toLowerCase());default:return true;}});
      } else if(cmd==='select-object') {const wanted=args.flatMap(x=>x.split(',')).filter(x=>x&&!x.startsWith('-'));pipe=values.map(value=>{if(!value||typeof value!=='object')return value;const result={};for(const key of wanted)if(key in value)result[key]=value[key];return Object.keys(result).length?result:value;});}
      else if(cmd==='sort-object') {const key=args.find(x=>!x.startsWith('-'));pipe=[...values].sort((a,b)=>String(a?.[key]??a).localeCompare(String(b?.[key]??b),undefined,{numeric:true}));if(args.some(x=>x.toLowerCase()==='-descending'))pipe.reverse();}
      else if(cmd==='measure-object') {const words=values.map(x=>String(x).trim().split(/\s+/).filter(Boolean).length).reduce((a,b)=>a+b,0);pipe=[{Count:values.length,Average:values.length?values.length:0,Sum:words}];}
      else if(cmd==='format-table'||cmd==='format-list') pipe=values;
      else if(cmd==='out-null')pipe=[];
      else if(cmd==='out-string')pipe=[values.map(v=>typeof v==='object'?Object.entries(v).map(([k,x])=>`${k}: ${x}`).join('  '):String(v)).join('\n')];
      else if(cmd==='tee-object') {const target=positional[0];if(target)this.put(session,target,values.map(v=>typeof v==='object'?JSON.stringify(v):String(v)).join('\n')+'\n');pipe=values;}
      else if(cmd==='foreach-object') {const script=args.join(' '),match=script.match(/\$?_\.([\w]+)/);pipe=values.map(v=>match?v?.[match[1]]:v);}
      else if(cmd==='convertto-json')pipe=[JSON.stringify(values,null,args.includes('-Depth')?4:2)];
      else if(cmd==='convertfrom-json')pipe=values.flatMap(v=>{try{const parsed=JSON.parse(v);return Array.isArray(parsed)?parsed:[parsed];}catch{return[];}});
      if(cmd!=='out-null'){pipe.forEach(value=>emit(value&&typeof value==='object'?Object.entries(value).map(([k,v])=>`${k}: ${v}`).join('  '):value));}
    }
    else if (cmd==='findstr'||cmd==='select-string') {const pattern=positional[0]||'', items=pipeInput||[];const matches=items.filter(x=>String(x).toLowerCase().includes(pattern.toLowerCase()));matches.forEach(v=>emit(v));pipe=matches;code=matches.length?0:1;}
    else if (cmd==='test-path') { const exists=!!this.node(session,pathArg);emit(exists?'True':'False');pipe=[exists];code=exists?0:1; }
    else if (cmd==='clear-host') { /* UI handles clear as a regular blank output event */ }
    else if (cmd==='exit') emit('Use the close tab control to finish this virtual session.','dim');
    else { emit(`${name}: command not found in the virtual ${bash?'Bash':'PowerShell'} environment. Try "help" for supported commands.`,'err');code=127; }
    if (redirect) { const prior=this.node(session,redirect);this.put(session,redirect,(append?prior?.data||'':'')+out.map(x=>x.text).join('\n')+'\n');return {output:[],code:0,pipe:[]}; }
    return {output:out,code,pipe};
  }
}

module.exports = { Simulator };
