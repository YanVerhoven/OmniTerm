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
const PACKAGE_CATALOG = {
  apt: { firefox:'1:1snap1-0ubuntu5', 'p7zip-full':'16.02+transitional.1', '7zip':'23.01+dfsg-11' },
  pip: { pip:'24.2', setuptools:'75.1.0', requests:'2.32.3', 'charset-normalizer':'3.4.0', idna:'3.10', urllib3:'2.2.3', certifi:'2024.8.30', numpy:'2.1.2', colorama:'0.4.6', flask:'3.0.3', blinker:'1.8.2', click:'8.1.7', itsdangerous:'2.2.0', jinja2:'3.1.4', markupsafe:'3.0.1', werkzeug:'3.0.4' },
  winget: { 'Mozilla.Firefox':{name:'Mozilla Firefox',version:'144.0'}, '7zip.7zip':{name:'7-Zip',version:'24.08'} },
  choco: { git:{name:'git',version:'2.47.0'}, '7zip':{name:'7zip',version:'24.08'}, vscode:{name:'vscode',version:'1.95.3'}, python:{name:'python',version:'3.12.7'} }
};

function newState(shell) {
  const stamp = now(), fsTree = {};
  const fsKey = p => shell==='bash'?p:(p.length===3&&p.endsWith(':/')?p.slice(0,2):p.toLowerCase());
  const s = {
    kind: shell, cwd: shell === 'bash' ? '/root' : 'C:/Deploy', home: shell === 'bash' ? '/root' : 'C:/Users/admin',
    clock: stamp, lastExit: 0, history: [], fs: fsTree,
    env: shell==='bash'?{COMPUTERNAME:'vm',OS:'Ubuntu 24.04.1 LTS',USER:'root',USERNAME:'root',HOME:'/root',SHELL:'/bin/bash',PWD:'/root',PATH:'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}:{COMPUTERNAME:'DESKTOP-LAB',OS:'Windows_NT',PROCESSOR_ARCHITECTURE:'AMD64',USERDOMAIN:'DESKTOP-LAB',USERNAME:'admin',USERPROFILE:'C:/Users/admin',HOME:'C:/Users/admin',HOMEDRIVE:'C:',HOMEPATH:'\\Users\\admin',TEMP:'C:/Users/admin/AppData/Local/Temp',TMP:'C:/Users/admin/AppData/Local/Temp',PATH:'C:/Windows/System32;C:/Windows;C:/Python312;C:/Deploy/tools'},
    vars: {}, packages: {}, packageManagers:{apt:{},pip:{pip:'24.2',setuptools:'75.1.0'},winget:{},choco:{}}, services: { ssh: 'running', cron: 'running', nginx: 'stopped', docker: 'stopped',
      BITS: 'running', EventLog: 'running', Spooler: 'running', wuauserv: 'stopped' },
    processes: { 4: 'System', 912: 'explorer', 1480: 'svchost', 2844: 'OmniTerm', 3140: 'python' },
    git: {}, aliases: {}, locationStack: [], mounts: {}, networkLatency: 25, networkLoss: 0
  };
  const mkdir = p => { fsTree[fsKey(p)] = { name: posix.basename(p) || p, dir: true, data: '', executable: false, modified: stamp }; };
  const write = (p, data, executable = false) => { fsTree[fsKey(p)] = { name: posix.basename(p), dir: false, data, executable, modified: stamp }; };
  if (shell === 'bash') {
    ['/','/root','/etc','/usr','/usr/bin','/var','/var/log','/tmp','/home'].forEach(mkdir);
    write('/etc/os-release', 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n');
    write('/var/log/syslog', 'OmniTerm virtual session started.\nVirtual filesystem ready.\n');
    write('/root/check.sh', '#!/bin/bash\n# Verify simulated package installation state\n', true);
    write('/root/deploy_linux.sh', '#!/bin/bash\n# Virtual apt deployment plan\napt-get update -qq\napt-get install -y firefox p7zip-full\napt-get upgrade -y\napt-get remove -y p7zip-full\n', true);
  } else {
    ['C:','C:/Deploy','C:/Deploy/logs','C:/Deploy/tools','C:/Users','C:/Users/admin','C:/Users/admin/AppData','C:/Users/admin/AppData/Local','C:/Users/admin/AppData/Local/Temp','C:/Windows','C:/Windows/System32','C:/Program Files','C:/Program Files/WindowsApps'].forEach(mkdir);
    write('C:/Deploy/Deploy-Apps.ps1', "param([ValidateSet('Install','Update','Uninstall')][string]$Action, [string]$Package)\n# Virtual winget deployment plan\n");
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
    if (!this.db.settings) this.db.settings = { theme: 'dark', fontSize: 14, speed: 1, ttlHours:12 };
    this.db.settings={theme:'dark',fontSize:14,speed:1,ttlHours:12,...this.db.settings};
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
        state.history=legacy.history||[];state.env={...state.env,...legacy.env};state.vars=legacy.vars||{};state.aliases=legacy.aliases||{};state.locationStack=legacy.location_stack||[];
        state.fs={};for(const [filePath,file] of Object.entries(legacy.fs||{})){const key=shell==='bash'?filePath:filePath.toLowerCase().replace(/:\/$/i,':');state.fs[key]={name:file.n,dir:!!file.d,data:file.data||'',executable:!!file.x,modified:file.t||now()};}
        state.services=Object.fromEntries(Object.entries(legacy.services||{}).map(([name,value])=>[name,String(value.Status||value.status||'stopped').toLowerCase()]));
        state.processes=Object.fromEntries(Object.entries(legacy.processes||{}).map(([pid,value])=>[pid,typeof value==='string'?value:(value.Name||value.name||`process-${pid}`)]));
        state.networkLatency=legacy.network_latency_ms??25;state.networkLoss=legacy.network_loss_pct??0;
        const packageSources={apt:legacy.apt,pip:legacy.pip,winget:legacy.winget,choco:legacy.choco};state.packages={};state.packageManagers={apt:{},pip:{pip:'24.2',setuptools:'75.1.0'},winget:{},choco:{}};
        for(const [manager,source]of Object.entries(packageSources))for(const [name,detail]of Object.entries(source||{})){const version=typeof detail==='string'?detail:(detail.ver||detail.version||detail.new||'installed');state.packageManagers[manager][name]=version;const key=manager==='winget'?name==='Mozilla.Firefox'?'firefox':name==='7zip.7zip'?'7zip':name:manager==='pip'?name.toLowerCase():name;state.packages[key]=version;}
        for(const [root,repo]of Object.entries(legacy.git_repos||{}))state.git[root]=this.importGit(repo);
        return {id:row.id,name:row.name,shell,type:row.session_type||'temporary',pinned:!!row.is_pinned,
          open:!!row.is_open,createdAt:row.created_at||now(),updatedAt:row.last_activity_at||now(),
          autoCleanup:!!row.auto_cleanup_enabled,expiresAt:row.auto_cleanup_enabled&&row.expires_at?row.expires_at:null,state,transcript:[]};
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
  deploy(session, platform, action, requested, emit) {
    const s=session.state,windows=platform==='ps',manager=windows?'winget':'apt',mode=String(action||'install').toLowerCase();
    s.packageManagers ||= {apt:{},pip:{pip:'24.2',setuptools:'75.1.0'},winget:{},choco:{}};s.packageManagers[manager] ||= {};
    const defaults=windows?['Mozilla.Firefox','7zip.7zip']:['firefox','p7zip-full'];
    const packages=requested.length?requested:defaults;
    if(!['install','update','uninstall','remove'].includes(mode)){emit(`Unsupported deployment action: ${action}`,'err');return 1;}
    const stages=mode==='uninstall'||mode==='remove'?['3.UNINSTALL','3.VERIFY']:mode==='update'?['0.INDEX','2.UPDATE']:['0.INDEX','1.INSTALL','1.VERIFY'];
    for(const stage of stages)emit(`[${new Date(s.clock).toISOString().replace('T',' ').slice(0,19)}] === ${stage}: START ===`,'dim');
    for(const packageName of packages){
      const key=windows?(packageName==='7zip.7zip'?'7zip':packageName==='Mozilla.Firefox'?'firefox':packageName):packageName;
      if(mode==='uninstall'||mode==='remove'){delete s.packages[key];delete s.packageManagers[manager][packageName];emit(`${key}: removed from virtual package state.`,'warn');}
      else{const version=windows?(key==='firefox'?'144.0':'24.08'):PACKAGE_CATALOG.apt[packageName]||'1.0.0';s.packages[key]=version;s.packageManagers[manager][packageName]=version;if(!windows&&packageName==='p7zip-full'){s.packageManagers.apt['7zip']=PACKAGE_CATALOG.apt['7zip'];s.packages['7zip']=PACKAGE_CATALOG.apt['7zip'];}emit(`${key}: ${mode} completed (simulated offline).`,'ok');}
    }
    emit('');emit(`[${new Date(s.clock).toISOString().replace('T',' ').slice(0,19)}] RESULT: SUCCESS`,'ok');emit('exit code: 0','ok');return 0;
  }
  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temp = this.file + '.tmp';
    fs.writeFileSync(temp, JSON.stringify(this.db), 'utf8');
    fs.renameSync(temp, this.file);
  }
  create(shell = 'ps', type = 'temporary', name, ttlHours=this.db.settings.ttlHours||12) {
    const created = now();
    const autoCleanup=type==='temporary';
    const s = { id: id(), name: name || (shell === 'bash' ? 'Ubuntu' : 'PowerShell'), shell,
      type, pinned: false, open: true, autoCleanup, createdAt: created, updatedAt: created,
      expiresAt: type === 'temporary' ? (autoCleanup?new Date(Date.now() + Number(ttlHours) * 3600000).toISOString():null) : null,
      state: newState(shell), transcript: [] };
    this.db.sessions.push(s); this.db.activeId = s.id; this.save(); return s;
  }
  current(idValue) { const s = this.db.sessions.find(x => x.id === idValue); if (!s) throw new Error('Session not found'); return s; }
  list() { return this.db.sessions.filter(x => x.open).sort((a,b) => Number(b.pinned)-Number(a.pinned) || a.createdAt.localeCompare(b.createdAt)).map(s => ({
    id:s.id,name:s.name,shell:s.shell,type:s.type,pinned:s.pinned,autoCleanup:s.autoCleanup!==false,expires:s.expiresAt,createdAt:s.createdAt,active:s.id===this.db.activeId,cwd:s.state.cwd,transcript:s.transcript
  })); }
  listClosed() { return this.db.sessions.filter(s=>!s.open).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt)).map(s=>({id:s.id,name:s.name,shell:s.shell,type:s.type,closedAt:s.closedAt||s.updatedAt})); }
  expire() {
    const time = Date.now(); const expired = this.db.sessions.filter(s => s.type === 'temporary' && s.autoCleanup!==false && s.expiresAt && Date.parse(s.expiresAt) <= time);
    for (const s of expired) {
      this.db.sessions = this.db.sessions.filter(x => x.id !== s.id);
      this.db.cleanup.unshift({ id:id(), sessionId:s.id, name:s.name, at:now(), event:'expired-session-cleanup', status:'completed', actions:['Removed virtual session state.'] });
      this.db.cleanup=this.db.cleanup.slice(0,200);
    }
  }
  auditCleanup(event='check-finished',actions=['Cleanup check completed.']) {
    this.db.cleanup.unshift({id:id(),sessionId:'__cleanup_worker__',name:'Session cleanup',at:now(),event,status:'completed',actions});
    this.db.cleanup=this.db.cleanup.slice(0,200);
  }
  handle(op, data = {}) {
    let result;
    if (op === 'list') { this.expire(); if (!this.db.sessions.some(s => s.open)) this.create('ps'); result = this.list(); }
    else if (op === 'settings') result = this.db.settings;
    else if (op === 'saveSettings') { this.db.settings = { ...this.db.settings, ...data.settings }; result = this.db.settings; }
    else if (op === 'create') { result = { id:this.create(data.shell, data.type, data.name, data.ttlHours).id }; }
    else if (op === 'history') result = this.db.cleanup.slice(0, 100);
    else if (op === 'listClosed') {this.expire();result=this.listClosed();}
    else if (op === 'save') result=true;
    else if (op === 'command') result = this.command(this.current(data.id), data.command || '');
    else {
      const s = this.current(data.id);
      if (op === 'rename') { const name = String(data.name || '').trim(); if (!name) throw new Error('Name cannot be empty'); s.name = name; }
      else if (op === 'activate') { this.db.activeId = s.id; }
      else if (op === 'close') { s.open = false; if (this.db.activeId === s.id) this.db.activeId = this.db.sessions.find(x => x.open)?.id; if (!this.db.sessions.some(x => x.open)) this.create('ps'); }
      else if (op === 'restore') { s.open=true;this.db.activeId=s.id; }
      else if (op === 'reset') { s.state=newState(s.shell);s.transcript=[];s.updatedAt=now(); }
      else if (op === 'delete') { this.db.sessions = this.db.sessions.filter(x => x.id !== s.id); this.db.cleanup.unshift({ id:id(),sessionId:s.id,name:s.name,at:now(),event:'manual-delete',status:'completed',actions:['Removed virtual session state.'] }); if (this.db.activeId === s.id) this.db.activeId=this.db.sessions.find(x=>x.open)?.id; if (!this.db.sessions.some(x => x.open)) this.create('ps'); }
      else if (op === 'pin') { if (data.pinned && s.type !== 'permanent') throw new Error('Convert this session to permanent before pinning it.'); s.pinned = !!data.pinned; }
      else if (op === 'permanent') { s.type = 'permanent'; s.autoCleanup=false;s.expiresAt = null;s.pinned=!!s.pinned; }
      else if (op === 'temporary') { s.type = 'temporary';s.autoCleanup=true;s.pinned=false;s.expiresAt = new Date(Date.now() + Number(data.hours || this.db.settings.ttlHours || 12) * 3600000).toISOString(); }
      else if (op === 'extend') { if (s.type !== 'temporary') throw new Error('Permanent sessions do not expire.');s.autoCleanup=true;s.expiresAt = new Date(Math.max(Date.now(), Date.parse(s.expiresAt || now())) + Number(data.hours || 12) * 3600000).toISOString(); }
      else if (op === 'autoCleanup') { if(s.type!=='temporary')throw new Error('Auto-cleanup applies only to temporary sessions.');s.autoCleanup=!!data.enabled;s.expiresAt=s.autoCleanup?new Date(Date.now()+Number(data.hours||this.db.settings.ttlHours||12)*3600000).toISOString():null; }
      else if (op === 'duplicate') {const copy=this.create(s.shell,'temporary',`${s.name} copy`);copy.state=JSON.parse(JSON.stringify(s.state));copy.autoCleanup=true;copy.transcript=[];result={id:copy.id};}
    else if (op === 'cleanup') { const before=this.db.sessions.length;this.expire();const removed=before-this.db.sessions.length;this.auditCleanup('check-finished',['Cleanup check completed.',`Removed sessions: ${removed}`]);result = this.db.cleanup.slice(0,100); }
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
  psStatements(source) {
    const statements=[];let start=0,depth=0,quote='',escape=false;
    for(let i=0;i<source.length;i++){
      const ch=source[i];if(escape){escape=false;continue;}if(ch==='`'&&quote!=='\''){escape=true;continue;}
      if(quote){if(ch===quote)quote='';continue;}if(ch==='"'||ch==="'"){quote=ch;continue;}
      if(ch==='{')depth++;else if(ch==='}')depth=Math.max(0,depth-1);
      else if((ch===';'||ch==='\n')&&depth===0){const statement=source.slice(start,i).trim();if(statement)statements.push(statement);start=i+1;}
    }
    const tail=source.slice(start).trim();if(tail)statements.push(tail);return statements;
  }
  psBlock(source,opening) {
    let depth=0,quote='',escape=false;
    for(let i=opening;i<source.length;i++){
      const ch=source[i];if(escape){escape=false;continue;}if(ch==='`'&&quote!=='\''){escape=true;continue;}
      if(quote){if(ch===quote)quote='';continue;}if(ch==='"'||ch==="'"){quote=ch;continue;}
      if(ch==='{')depth++;else if(ch==='}'&&--depth===0)return{body:source.slice(opening+1,i),end:i+1};
    }
    return null;
  }
  psValues(session,source) {
    let value=source.trim();
    if((value.startsWith('@(')||value.startsWith('('))&&value.endsWith(')'))value=value.slice(value.indexOf('(')+1,-1);
    const variable=value.match(/^\$([\w:]+)$/);if(variable){const key=variable[1];return String(key.toLowerCase().startsWith('env:')?session.state.env[key.slice(4).toUpperCase()]??'':session.state.vars[key]??'').split(',').filter(Boolean);}
    return splitOutside(value,[',']).map(part=>part.text.trim().replace(/^['"]|['"]$/g,'')).filter(Boolean);
  }
  psCondition(session,source) {
    const condition=source.trim().replace(/^\(|\)$/g,'').trim();
    const logical=condition.match(/^(.+?)\s+-(and|or)\s+(.+)$/i);if(logical)return logical[2].toLowerCase()==='and'?this.psCondition(session,logical[1])&&this.psCondition(session,logical[3]):this.psCondition(session,logical[1])||this.psCondition(session,logical[3]);
    if(/^!/.test(condition))return !this.psCondition(session,condition.slice(1));
    const testPath=condition.match(/^Test-Path\s+(.+)$/i);if(testPath)return!!this.node(session,testPath[1].replace(/^['"]|['"]$/g,''));
    const match=condition.match(/^\$?([\w:]+)\s+-(eq|ne|gt|ge|lt|le|like|notlike|match|notmatch)\s+(.+)$/i);
    if(match){const leftKey=match[1],left=leftKey.toLowerCase().startsWith('env:')?session.state.env[leftKey.slice(4).toUpperCase()]??'':session.state.vars[leftKey]??'',right=match[3].trim().replace(/^['"]|['"]$/g,''),operator=match[2].toLowerCase(),a=Number(left),b=Number(right);
      switch(operator){case'eq':return String(left).toLowerCase()===right.toLowerCase();case'ne':return String(left).toLowerCase()!==right.toLowerCase();case'gt':return a>b;case'ge':return a>=b;case'lt':return a<b;case'le':return a<=b;case'like':case'notlike':{const regex=new RegExp('^'+right.replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$','i'),found=regex.test(String(left));return operator==='like'?found:!found;}case'match':return String(left).toLowerCase().includes(right.toLowerCase());case'notmatch':return !String(left).toLowerCase().includes(right.toLowerCase());}}
    const normalized=condition.replace(/^['"]|['"]$/g,'').toLowerCase();return !['','false','0','$false'].includes(normalized);
  }
  bindPowerShellParameters(session,source,args) {
    const paramAt=source.search(/^\s*param\s*\(/im);
    if(paramAt<0)return source;
    const open=source.indexOf('(',paramAt);let depth=0,quote='',escape=false,close=-1;
    for(let i=open;i<source.length;i++){
      const ch=source[i];if(escape){escape=false;continue;}if(ch==='`'&&quote!=="'"){escape=true;continue;}
      if(quote){if(ch===quote)quote='';continue;}if(ch==='"'||ch==="'"){quote=ch;continue;}
      if(ch==='(')depth++;else if(ch===')'&&--depth===0){close=i;break;}
    }
    if(close<0)throw new Error('PowerShell param block has unclosed parentheses.');
    const parameterSource=source.slice(open+1,close),declarations=[];let start=0,nested=0,paramQuote='',escaped=false;
    for(let i=0;i<parameterSource.length;i++){const ch=parameterSource[i];if(escaped){escaped=false;continue;}if(ch==='`'&&paramQuote!=="'"){escaped=true;continue;}if(paramQuote){if(ch===paramQuote)paramQuote='';continue;}if(ch==='"'||ch==="'"){paramQuote=ch;continue;}if(ch==='('||ch==='[')nested++;else if(ch===')'||ch===']')nested=Math.max(0,nested-1);else if(ch===','&&nested===0){declarations.push(parameterSource.slice(start,i).trim());start=i+1;}}
    declarations.push(parameterSource.slice(start).trim());
    const named=new Map();const positional=[];
    for(let i=0;i<args.length;i++){
      if(args[i].startsWith('-')){const [rawKey,inlineValue]=args[i].slice(1).split(/:(.*)/s);const key=rawKey.toLowerCase();named.set(key,inlineValue??(args[i+1]&&!args[i+1].startsWith('-')?args[++i]:'$true'));}
      else positional.push(args[i]);
    }
    let pos=0;
    for(const declaration of declarations){
      const variable=declaration.match(/\$([\w]+)/);if(!variable)continue;
      const name=variable[1],key=name.toLowerCase(),validate=declaration.match(/\[ValidateSet\(([^)]*)\)\]/i),defaultMatch=declaration.match(/\$[\w]+\s*=\s*([^,]+)$/);
      let value=named.has(key)?named.get(key):positional[pos++]??(defaultMatch?defaultMatch[1].trim().replace(/^['"]|['"]$/g,''):'');
      value=String(value).replace(/^['"]|['"]$/g,'');
      if(validate){const allowed=[...validate[1].matchAll(/(['"])(.*?)\1/g)].map(match=>match[2]);const match=allowed.find(item=>item.toLowerCase()===value.toLowerCase());if(!match)throw new Error(`Parameter -${name} must be one of: ${allowed.join(', ')}.`);value=match;}
      session.state.vars[name]=value;session.state.vars[key]=value;
    }
    return source.slice(0,paramAt)+source.slice(close+1);
  }
  runPowerShellScript(session,source,args=[]) {
    const output=[];let code=0;const executeBody=body=>{for(const statement of this.psStatements(body)){if(statement.startsWith('#')||/^param\s*\(/i.test(statement))continue;
      const ifMatch=statement.match(/^if\s*\((.*?)\)\s*\{/is);
      const eachMatch=statement.match(/^foreach\s*\(\s*\$([\w]+)\s+in\s+([\s\S]+?)\s*\)\s*\{/i);
      if(ifMatch){const opening=statement.indexOf('{',ifMatch[0].length-1),trueBlock=this.psBlock(statement,opening);if(!trueBlock)throw new Error('Script has an unclosed if block.');let rest=statement.slice(trueBlock.end).trim(),falseBody='';if(/^else\b/i.test(rest)){const elseOpen=rest.indexOf('{'),elseBlock=this.psBlock(rest,elseOpen);if(!elseBlock)throw new Error('Script has an unclosed else block.');falseBody=elseBlock.body;rest=rest.slice(elseBlock.end).trim();}if(rest)throw new Error('Unsupported syntax after if block.');executeBody(this.psCondition(session,ifMatch[1])?trueBlock.body:falseBody);continue;}
      if(eachMatch){const opening=statement.indexOf('{',eachMatch[0].length-1),block=this.psBlock(statement,opening);if(!block||statement.slice(block.end).trim())throw new Error('Script has an unclosed foreach block.');const range=eachMatch[2].trim().match(/^(-?\d+)\.\.(-?\d+)$/);let values;if(range){const start=Number(range[1]),end=Number(range[2]),step=start<=end?1:-1;values=[];for(let n=start;step>0?n<=end:n>=end;n+=step)values.push(String(n));}else values=this.psValues(session,eachMatch[2]);if(values.length>100)throw new Error('Script iteration limit is 100.');for(const value of values){session.state.vars[eachMatch[1]]=String(value);executeBody(block.body);if(code!==0)break;}continue;}
      const result=this.runCommand(session,statement,null);output.push(...result.output);code=result.code;if(code!==0)break;
    }};
    try{const script=this.bindPowerShellParameters(session,source,args);executeBody(script.split(/\r?\n/).filter(row=>!row.trim().startsWith('#')).join('\n'));}catch(error){output.push(textResult(error.message,'err'));code=1;}
    return{output,code};
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
        const normalizedCommand=session.shell==='ps'?segment.toLowerCase():segment;
        if (['clear','cls','clear-host'].includes(normalizedCommand)) { session.transcript=[]; output=[];previousCode=0;continue; }
        if (normalizedCommand==='exit'||(session.shell==='bash'&&normalizedCommand==='logout')) { output.push(textResult('Use the tab close button to close this virtual session.','dim'));previousCode=0;continue; }
        let pipeline=splitOutside(segment,['|']).map(part=>part.text).filter(Boolean); let pipeInput=null;
        for (let i=0;i<pipeline.length;i++) {
          const result=this.runCommand(session,pipeline[i],pipeInput); if(i===pipeline.length-1)output.push(...result.output); pipeInput=result.pipe ?? result.output.map(x=>x.text); s.lastExit=result.code;previousCode=result.code;
        }
      }
    } catch (error) { output.push(textResult(error.message,'err')); s.lastExit=1; }
    session.updatedAt=now(); session.transcript.push(...output.map(line=>({type:'output',...line,at:now()})));
    if (session.transcript.length>2500) session.transcript=session.transcript.slice(-2500);
    this.save(); return {output,cwd:s.cwd,exitCode:s.lastExit,activeId:this.db.activeId,transcript:session.transcript};
  }
  packageCommand(session,command,args) {
    const state=session.state,output=[],emit=(value,style='out')=>output.push(textResult(value,style));
    const pythonPip=command==='python',manager=pythonPip||command==='pip3'?'pip':command==='apt-get'||command==='apt-cache'||command==='dpkg'?'apt':command;
    state.packageManagers ||= {apt:{},pip:{pip:'24.2',setuptools:'75.1.0'},winget:{},choco:{}};
    for(const name of ['apt','pip','winget','choco'])state.packageManagers[name] ||= {};
    const stores=state.packageManagers,installed=stores[manager];
    for(const [name,version]of Object.entries(state.packages||{}))if(!Object.values(stores).some(store=>Object.hasOwn(store,name))) {
      const owner=Object.hasOwn(PACKAGE_CATALOG.pip,name)?'pip':Object.hasOwn(PACKAGE_CATALOG.apt,name)?'apt':Object.hasOwn(PACKAGE_CATALOG.choco,name)?'choco':Object.hasOwn(PACKAGE_CATALOG.winget,name)?'winget':name==='firefox'||name==='7zip'? 'apt':null;
      if(owner)stores[owner][name]=version;
    }
    const aliases={'Mozilla.Firefox':'firefox','7zip.7zip':'7zip'};
    const globalKey=(source,name)=>source==='winget'?aliases[name]||name:name.toLowerCase();
    const savePackage=(name,version)=>{installed[name]=version;state.packages[globalKey(manager,name)]=version;};
    const removePackage=name=>{delete installed[name];const key=globalKey(manager,name);if(!Object.values(stores).some(store=>Object.keys(store).some(item=>globalKey(Object.keys(stores).find(keyName=>stores[keyName]===store),item)===key)))delete state.packages[key];};
    const positional=args.filter(item=>!item.startsWith('-'));
    if(manager==='apt') {
      let verb=pythonPip?'':args[0]||'',values=pythonPip?[]:args.slice(1);
      if(command==='apt-cache'){verb='search';values=args.slice(1);}
      if(command==='dpkg'){verb=args[0]==='-l'||args[0]==='--list'?'list':args[0]==='-s'||args[0]==='--status'?'show':args[0]==='-r'?'remove':args[0];values=args.slice(1);}
      const names=values.filter(x=>!x.startsWith('-')&&!['--installed','--upgradable'].includes(x));
      if(command==='dpkg'&&verb==='list'){emit('Desired=Unknown/Install/Remove/Purge/Hold');emit('||/ Name           Version                Architecture Description');emit('+++-==============-======================-============-================================');for(const [name,version]of Object.entries(installed))emit(`ii  ${name.padEnd(14)} ${version.padEnd(22)} amd64        installed by simulator`);return{output,code:0};}
      if(command==='dpkg'&&verb==='show'){const name=names[0];if(installed[name])emit(`Package: ${name}\nStatus: install ok installed\nArchitecture: amd64\nVersion: ${installed[name]}`);else emit(`dpkg-query: package '${name||''}' is not installed and no information is available`,'warn');return{output,code:installed[name]?0:1};}
      if(verb==='--version'||!verb){emit('apt 2.7.14ubuntu1 (amd64)','dim');}
      else if(verb==='update'){emit('Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease');emit('Get:2 http://archive.ubuntu.com/ubuntu noble-updates InRelease (offline sample)');emit('Reading package lists... Done');emit('12 packages can be upgraded. Run apt list --upgradable to see them.','dim');}
      else if(verb==='upgrade'||verb==='full-upgrade'||verb==='dist-upgrade'){
        for(const [name,version]of Object.entries(installed))if(PACKAGE_CATALOG.apt[name])savePackage(name,PACKAGE_CATALOG.apt[name]);
        emit('Reading package lists... Done');emit(`0 upgraded, 0 newly installed, 0 to remove and 12 not upgraded.`);
      }
      else if(['install','reinstall'].includes(verb)){
        if(!names.length)emit('E: Package name required','err');
        else {let missing=false;for(const name of names){if(!Object.hasOwn(PACKAGE_CATALOG.apt,name)){emit(`E: Unable to locate package ${name}`,'err');missing=true;continue;}const dependencies=name==='p7zip-full'?['7zip']:[];for(const dependency of dependencies)if(!installed[dependency])savePackage(dependency,PACKAGE_CATALOG.apt[dependency]);savePackage(name,PACKAGE_CATALOG.apt[name]);emit(`${name} (${PACKAGE_CATALOG.apt[name]}) installed in virtual package state.`,'ok');}if(missing)output.push(textResult('','dim'));}
      }
      else if(['remove','purge','autoremove'].includes(verb)){if(!names.length)emit(`E: Package name required`,'err');for(const name of names){if(installed[name]){removePackage(name);emit(`Removing ${name} from virtual package state.`,'ok');}else emit(`Package '${name}' is not installed, so not removed.`,'dim');}}
      else if(verb==='list'){const filter=names.find(x=>x!=='--installed'&&x!=='--upgradable'),installedOnly=values.includes('--installed');emit('Listing...');for(const [name,version]of Object.entries(PACKAGE_CATALOG.apt).filter(([key])=>(!filter||key.includes(filter))&&(!installedOnly||installed[key])).map(([key,value])=>[key,installed[key]||value]))emit(`${name}/${installed[name]?'now':'noble'} ${version} amd64 ${installed[name]?'[installed]':''}`);}
      else if(verb==='search'){const pattern=(names[0]||'').toLowerCase(),results=Object.entries(PACKAGE_CATALOG.apt).filter(([name])=>name.toLowerCase().includes(pattern));for(const [name,version]of results)emit(`${name} - virtual package metadata (${version})`);if(!results.length)emit('Sorting... Done\nFull Text Search... Done\n(no results)','dim');}
      else if(verb==='show'||verb==='info'){for(const name of names){const version=PACKAGE_CATALOG.apt[name];if(version)emit(`Package: ${name}\nVersion: ${version}\nArchitecture: amd64\nDescription: Offline virtual package metadata`);else emit(`N: Unable to locate package ${name}`,'warn');}}
      else if(verb==='list'&&command==='dpkg'){}else emit(`E: Invalid operation ${verb}`,'err');
      return{output,code:output.some(item=>item.style==='err')?1:0};
    }
    if(manager==='pip') {
      const argv=pythonPip?args.slice(2):args,verb=argv[0]||'--version',names=argv.slice(1).filter(x=>!x.startsWith('-'));
      if(verb==='--version'||verb==='-V'){emit('pip 24.2 from /usr/lib/python3/dist-packages/pip (python 3.12)','dim');}
      else if(verb==='list'){const rows=Object.entries(installed).sort(([a],[b])=>a.localeCompare(b));const width=Math.max(7,...rows.map(([name])=>name.length));emit(`${'Package'.padEnd(width)} Version`);emit(`${'-'.repeat(width)} ---------`);for(const [name,version]of rows)emit(`${name.padEnd(width)} ${version}`);}
      else if(verb==='freeze'){for(const [name,version]of Object.entries(installed).sort(([a],[b])=>a.localeCompare(b)))emit(`${name}==${version}`);}
      else if(verb==='show'){for(const raw of names){const name=raw.toLowerCase(),version=installed[name];if(version)emit(`Name: ${name}\nVersion: ${version}\nLocation: /usr/lib/python3/dist-packages`);else emit(`WARNING: Package(s) not found: ${raw}`,'warn');}}
      else if(verb==='install'){if(!names.length)emit('ERROR: You must give at least one requirement to install','err');for(const raw of names){const [name,requested]=raw.toLowerCase().split('=='),version=PACKAGE_CATALOG.pip[name];if(!version){emit(`ERROR: Could not find a version that satisfies the requirement ${raw} (from versions: none)`,'err');emit(`ERROR: No matching distribution found for ${raw}`,'err');continue;}const actual=requested||version;if(installed[name]){emit(`Requirement already satisfied: ${name} (${installed[name]})`,'dim');continue;}savePackage(name,actual);if(name==='requests'){for(const dependency of ['charset-normalizer','idna','urllib3','certifi'])savePackage(dependency,PACKAGE_CATALOG.pip[dependency]);}if(name==='flask'){for(const dependency of ['blinker','click','itsdangerous','jinja2','markupsafe','werkzeug'])savePackage(dependency,PACKAGE_CATALOG.pip[dependency]);}emit(`Successfully installed ${name}-${actual}`,'ok');}}
      else if(verb==='uninstall'){for(const raw of names){const name=raw.toLowerCase();if(installed[name]){removePackage(name);emit(`Successfully uninstalled ${name}`,'ok');}else emit(`WARNING: Skipping ${raw} as it is not installed.`,'warn');}}
      else emit(`ERROR: unknown command "${verb}"`,'err');
      return{output,code:output.some(item=>item.style==='err')?1:0};
    }
    if(manager==='winget'||manager==='choco') {
      const verb=(args[0]||'help').toLowerCase(),idAt=args.findIndex(x=>['--id','-id'].includes(x.toLowerCase())),rawName=idAt>=0?args[idAt+1]:args.slice(1).find(x=>!x.startsWith('-')),catalog=PACKAGE_CATALOG[manager],known=Object.keys(catalog),findName=value=>known.find(name=>name.toLowerCase()===String(value||'').toLowerCase())||known.find(name=>catalog[name].name.toLowerCase()===String(value||'').toLowerCase());
      if(verb==='--version'||verb==='-v'||verb==='version'){emit(manager==='winget'?'v1.9.25200':'2.3.0','dim');}
      else if(verb==='list'||verb==='installed'){const rows=Object.entries(installed);if(!rows.length)emit(manager==='winget'?'No installed package found matching input criteria.':'Chocolatey has determined 0 package(s) are installed.','dim');else for(const [name,version]of rows){const detail=catalog[name]||{name,version};emit(manager==='winget'?`${detail.name}  ${name}  ${version}  winget`:`${name} ${version}`);}}
      else if(verb==='search'||verb==='info'||verb==='show'){const pattern=String(rawName||'').toLowerCase(),rows=known.filter(name=>!pattern||name.toLowerCase().includes(pattern)||catalog[name].name.toLowerCase().includes(pattern));for(const name of rows)emit(`${catalog[name].name}  ${name}  ${catalog[name].version}`);if(!rows.length)emit('No package found matching input criteria.','dim');}
      else if(['install','upgrade','uninstall','remove'].includes(verb)){
        let names=rawName?[findName(rawName)||rawName]:[];if(manager==='winget'&&verb==='upgrade'&&args.some(x=>['--all','-a'].includes(x.toLowerCase())))names=Object.keys(installed);
        if(manager==='choco'&&['upgrade','uninstall','remove'].includes(verb)&&names[0]==='all')names=Object.keys(installed);
        if(!names.length){emit(`${manager}: package identifier required.`,'err');return{output,code:1};}
        for(const name of names){const item=catalog[name];if(!item){emit(manager==='winget'?'No package found matching input criteria.':`Chocolatey was unable to find a package named '${name}'.`,'err');continue;}
          if(['uninstall','remove'].includes(verb)){if(!installed[name]){emit(`${name} is not installed.`,'warn');continue;}if(args.some(x=>x.toLowerCase()==='-whatif'||x.toLowerCase()==='--whatif')){emit(`What if: Uninstalling virtual package ${name}.`,'dim');continue;}removePackage(name);emit(manager==='winget'?`${item.name} successfully uninstalled.`:`${name} has been virtually uninstalled.`,'ok');continue;}
          if(verb==='upgrade'&&!installed[name]){emit(`${name} is not installed; use install.`,'warn');continue;}
          if(verb==='install'&&installed[name]){emit(`${name} v${installed[name]} is already installed.`,'dim');continue;}
          if(args.some(x=>x.toLowerCase()==='-whatif'||x.toLowerCase()==='--whatif')){emit(`What if: Installing virtual package ${name} v${item.version}.`,'dim');continue;}
          savePackage(name,item.version);emit(`${item.name} v${item.version} has been virtually ${verb==='upgrade'?'upgraded':'installed'}.`,'ok');
        }
      }
      else emit(manager==='choco'?'Chocolatey v2.3.0. Commands: install, upgrade, uninstall, list, search.':'Use winget install|upgrade|uninstall|list|search.','dim');
      return{output,code:output.some(item=>item.style==='err')?1:0};
    }
    emit(`Unsupported package manager: ${command}`,'err');return{output,code:1};
  }
  runCommand(session, source, pipeInput) {
    const sessionState=session.state, bash=session.shell==='bash';
    const assignment=source.match(/^\$([\w:]+)\s*=\s*(.*)$/);
    if(assignment){const value=assignment[2].replace(/^['"]|['"]$/g,'');if(assignment[1].toLowerCase().startsWith('env:'))sessionState.env[assignment[1].slice(4).toUpperCase()]=value;else sessionState.vars[assignment[1]]=value;return{output:[],code:0};}
    let redirect=null, append=false;
    const redirectParts=splitOutside(source,['>>','>']),redirectIndex=redirectParts.findIndex(part=>part.operator==='>'||part.operator==='>>');
    if(redirectIndex>0){append=redirectParts[redirectIndex].operator==='>>';redirect=redirectParts[redirectIndex].text.trim().replace(/^['"]|['"]$/g,'');source=redirectParts.slice(0,redirectIndex).map(part=>part.text).join(' ').trim();}
    const argv=tokenize(source); if (!argv.length) return {output:[],code:0};
    let name=argv.shift(), args=argv, cmd=bash?name:name.toLowerCase();
    const aliases={ls:'Get-ChildItem',dir:'Get-ChildItem',gci:'Get-ChildItem',cd:'Set-Location',sl:'Set-Location',chdir:'Set-Location',pwd:'Get-Location',gl:'Get-Location',cat:'Get-Content',type:'Get-Content',gc:'Get-Content',echo:'Write-Output',write:'Write-Output',rm:'Remove-Item',del:'Remove-Item',erase:'Remove-Item',rmdir:'Remove-Item',ri:'Remove-Item',cp:'Copy-Item',copy:'Copy-Item',cpi:'Copy-Item',mv:'Move-Item',move:'Move-Item',mi:'Move-Item',ren:'Rename-Item',rni:'Rename-Item',mkdir:'New-Item',md:'New-Item',ni:'New-Item',cls:'Clear-Host',where:'Where-Object','?':'Where-Object',select:'Select-Object',sort:'Sort-Object',measure:'Measure-Object','%':'ForEach-Object',date:'Get-Date',history:'Get-History',h:'Get-History',man:'Get-Help',help:'Get-Help',sleep:'Start-Sleep',gps:'Get-Process',ps:'Get-Process',gsv:'Get-Service',sls:'Select-String',pushd:'Push-Location',popd:'Pop-Location',gi:'Get-Item',gp:'Get-ItemProperty',sc:'Set-Content',ac:'Add-Content',clc:'Clear-Content',sp:'Set-ItemProperty',gm:'Get-Member',...s.aliases};
    if (!bash) cmd=(aliases[cmd]||name).toLowerCase();
    const out=[]; const emit=(v,style='out')=>out.push(textResult(v,style));
    const positional=args.filter(a=>!a.startsWith('-')&&(bash||!a.startsWith('/')));
    let pathArg=positional[0]||'.';
    let code=0, pipe=undefined;
    const scriptPath=['bash','sh','source'].includes(cmd)?positional[0]:name;
    if ((['bash','sh','source'].includes(cmd)||/\.(sh|ps1)$/i.test(name)||name.startsWith('./')||name.startsWith('.\\'))&&scriptPath) {
      const file=this.node(session,scriptPath);if(!file||file.dir)throw new Error(`Script not found: ${scriptPath}`);
      const scriptName=posix.basename(scriptPath).toLowerCase();
      if(scriptName==='deploy-apps.ps1'){const actionAt=args.findIndex(x=>x.toLowerCase()==='-action'),packageAt=args.findIndex(x=>x.toLowerCase()==='-package'),action=actionAt>=0?args[actionAt+1]:args[0]||'Install',packages=packageAt>=0?[args[packageAt+1]]:[];code=this.deploy(session,'ps',action,packages,emit);}
      else if(scriptName==='deploy_linux.sh'){code=this.deploy(session,'bash','install',[],emit);delete s.packages['p7zip-full'];delete s.packageManagers?.apt?.['p7zip-full'];emit('p7zip-full: removed by the virtual deployment plan.','warn');}
      else if(scriptName==='check.sh'){
        for(const pkg of ['firefox','p7zip-full'])emit(`${pkg}: ${s.packages[pkg]?'installed ('+s.packages[pkg]+')':'not installed'}`);
        for(const bin of ['firefox','7z'])emit(`${s.packages[bin==='7z'?'p7zip-full':bin]?'/usr/bin/'+bin:bin+' -> not found'}`);
      } else if(session.shell==='ps') {const scriptArgs=scriptPath===name?args:args.slice(1);const result=this.runPowerShellScript(session,file.data,scriptArgs);out.push(...result.output);code=result.code;}
      else for(const scriptLine of file.data.split(/\r?\n|\s*;\s*/)){
        const statement=scriptLine.trim();if(!statement||statement.startsWith('#')||statement.startsWith('//'))continue;
        const result=this.runCommand(session,statement,null);out.push(...result.output);code=result.code;if(code!==0)break;
      }
    }
    else if(cmd==='session'){
      const action=(args[0]||'').toLowerCase(),rows=this.db.sessions;
      if(action==='list'){for(const row of rows)emit(`${row.id}  ${row.name}  ${row.type.toUpperCase()}  ${row.expiresAt||'no expiry'}`);if(!rows.length)emit('No sessions.','dim');}
      else if(action==='create'){
        const type=(args[1]||'temporary').toLowerCase(),shell=args.some(x=>x.toLowerCase()==='bash')?'bash':args.some(x=>x.toLowerCase()==='ps')?'ps':session.shell;
        if(!['temporary','permanent'].includes(type))throw new Error('Usage: session create temporary|permanent [bash|ps]');
        const created=this.create(shell,type);emit(`Created virtual session ${created.id}`,'ok');
      }
      else if(action==='use'){
        const query=(args[1]||'').toLowerCase(),target=rows.find(x=>x.id.toLowerCase()===query||x.name.toLowerCase()===query);
        if(!target)throw new Error(`Session not found: ${args[1]||''}`);target.open=true;this.db.activeId=target.id;emit(`Active session: ${target.name}`,'ok');
      }
      else if(action==='extend'){
        const query=(args[1]||'').toLowerCase(),target=rows.find(x=>x.id.toLowerCase()===query||x.name.toLowerCase()===query),duration=(args[2]||'12h').match(/^(\d+(?:\.\d+)?)(h|d)$/i);
        if(!target)throw new Error(`Session not found: ${args[1]||''}`);if(!duration)throw new Error('Use a duration such as 12h or 7d.');
        if(target.type!=='temporary')throw new Error('Permanent sessions do not expire.');target.autoCleanup=true;target.expiresAt=new Date(Math.max(Date.now(),Date.parse(target.expiresAt||now()))+Number(duration[1])*(duration[2].toLowerCase()==='d'?24:1)*3600000).toISOString();emit('Session expiry extended.','ok');
      }
      else if(action==='make-permanent'||action==='make_permanent'){
        const query=(args[1]||'').toLowerCase(),target=rows.find(x=>x.id.toLowerCase()===query||x.name.toLowerCase()===query);if(!target)throw new Error(`Session not found: ${args[1]||''}`);target.type='permanent';target.autoCleanup=false;target.expiresAt=null;emit('Session is now permanent.','ok');
      }
      else if(action==='delete'){
        const query=(args[1]||'').toLowerCase(),target=rows.find(x=>x.id.toLowerCase()===query||x.name.toLowerCase()===query);if(!target)throw new Error(`Session not found: ${args[1]||''}`);
        this.db.sessions=this.db.sessions.filter(x=>x.id!==target.id);this.db.cleanup.unshift({id:id(),sessionId:target.id,name:target.name,at:now(),event:'manual-delete',status:'completed',actions:['Removed virtual session state.']});if(this.db.activeId===target.id)this.db.activeId=this.db.sessions.find(x=>x.open)?.id;emit('Session deleted.','ok');
      }
      else if(action==='cleanup'){this.expire();emit('Cleanup check completed.','ok');}
      else emit('Usage: session create|list|use|extend|make-permanent|delete|cleanup','warn');
    }
    else if(cmd==='sudo'){
      if(!args.length)emit('sudo: command is required; privilege changes do not affect the host.','warn');
      else if(['su','-i','-s'].includes(args[0])){emit('sudo: interactive privilege escalation is unavailable in the isolated simulator.','warn');code=126;}
      else {const result=this.runCommand(session,args.join(' '),pipeInput);out.push(...result.output);pipe=result.pipe;code=result.code;}
    }
    else if(cmd==='su'){emit('su: switching host users is unavailable in the isolated simulator.','warn');code=126;}
    else if(['curl','wget','ssh','scp','nmap','masscan','gobuster','ffuf','dirsearch','nikto','sqlmap','hydra','john','hashcat','tcpdump','tshark','wireshark','enum4linux','smbclient','smbmap','rpcclient','responder','arpspoof','bettercap','aircrack-ng','wpscan','sublist3r','amass','httprobe','chkrootkit','rkhunter','linpeas.sh','msfconsole','searchsploit','invoke-mimikatz','invoke-kerberoast','invoke-bloodhound','nc','netcat','traceroute','tracepath','dig','nslookup','host','whois'].includes(cmd)){
      emit(`${name}: network access is disabled; this simulator does not contact external hosts.`,'warn');code=126;
    }
    else if(cmd==='mount'){
      if(!args.length){emit('virtualfs on / type sandbox (rw)');Object.entries(s.mounts||{}).forEach(([device,target])=>emit(`${device} on ${target} type virtual (rw)`));}
      else {const values=args.filter(value=>!value.startsWith('-'));if(values.length<2||!this.node(session,values[1])?.dir)throw new Error('mount: target must be a directory in the virtual filesystem');s.mounts=s.mounts||{};s.mounts[values[0]]=this.resolve(session,values[1]);emit(`Mounted ${values[0]} in the virtual filesystem.`,'info');}
    }
    else if(cmd==='umount'){
      const target=args.find(value=>!value.startsWith('-'))||'',mounts=s.mounts||{},device=Object.keys(mounts).find(key=>key===target||mounts[key]===this.resolve(session,target));
      if(!device)throw new Error(`umount: ${target} is not a simulated mount point`);delete mounts[device];
    }
    else if(cmd==='chown'||cmd==='chgrp'){
      const values=args.filter(value=>!value.startsWith('-')),owner=values.shift();if(!owner||!values.length)throw new Error(`${cmd}: expected an owner/group and at least one path`);
      for(const target of values){const node=this.node(session,target);if(!node)throw new Error(`${cmd}: cannot access '${target}': No such file or directory`);node.owner=owner;}
    }
    else if(cmd==='rsync'){
      const paths=args.filter(value=>!value.startsWith('-'));if(paths.length!==2||paths[1].includes(':')){emit('rsync: remote transfers are disabled in the isolated simulator.','warn');code=126;}
      else {const result=this.runCommand(session,`cp -r ${paths.map(value=>/\s/.test(value)?`"${value}"`:value).join(' ')}`,pipeInput);out.push(...result.output);code=result.code;pipe=result.pipe;}
    }
    else if(['fdisk','mkfs','fsck','iptables','ufw','systemctl-enable'].includes(cmd)){
      emit(`${name}: administrative changes to the host are blocked in the virtual environment.`,'warn');code=126;
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
      const wildcard=/[*?]/.test(pathArg),force=args.some(a=>a.toLowerCase()==='-force'||a.toLowerCase()==='-hidden'||a.toLowerCase()==='-a'),recurse=args.some(a=>a.toLowerCase()==='-recurse'||a.toLowerCase()==='-r');let entries;
      if(wildcard){const directory=this.resolve(session,posix.dirname(pathArg)),pattern=posix.basename(pathArg),regex=new RegExp('^'+pattern.replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$',bash?'':'i');entries=this.children(session,directory).filter(item=>regex.test(item.name));if(!entries.length)throw new Error(`Path not found: ${pathArg}`);}
      else {const target=this.resolve(session,pathArg),node=this.node(session,target);if(!node)throw new Error(`Path not found: ${pathArg}`);entries=node.dir?this.children(session,target):[node];if(recurse&&node.dir){const pending=[target];while(pending.length){const parent=pending.shift();for(const child of this.children(session,parent)){entries.push(child);if(child.dir)pending.push(posix.join(parent,child.name));}}}}
      const flagValue=key=>{const at=args.findIndex(a=>a.toLowerCase()===`-${key.toLowerCase()}`);return at>=0?args[at+1]:undefined;};
      const matches=(value,pattern)=>!pattern||new RegExp('^'+String(pattern).replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$',bash?'':'i').test(value);
      let visible=entries.filter(n=>force||!n.name.startsWith('.'));
      if(!bash){if(args.some(a=>a.toLowerCase()==='-file'))visible=visible.filter(n=>!n.dir);if(args.some(a=>a.toLowerCase()==='-directory'))visible=visible.filter(n=>n.dir);const pattern=flagValue('Filter')||flagValue('Include');if(pattern)visible=visible.filter(n=>matches(n.name,pattern));const exclude=flagValue('Exclude');if(exclude)visible=visible.filter(n=>!matches(n.name,exclude));}
      const namesOnly=!bash&&args.some(a=>a.toLowerCase()==='-name'),longFormat=bash&&args.some(a=>a.startsWith('-')&&a.includes('l'));
      if(longFormat){emit(`total ${4*(visible.length+1)}`);for(const item of visible){const date=new Date(item.modified||now()).toLocaleString('en-US',{month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).replace(',',''),mode=item.dir?'drwxr-xr-x':item.executable?'-rwxr-xr-x':'-rw-r--r--',size=item.dir?4096:Buffer.byteLength(item.data||'','utf8');emit(`${mode} 1 ${item.owner||'root'} root ${String(size).padStart(5)} ${date} ${item.name}`,item.dir?'info':item.executable?'ok':'out');}}
      else if(bash){if(visible.length)emit(visible.map(item=>item.name).join('  '));}
      else visible.forEach(item=>emit(namesOnly?item.name:(item.dir?'📁 ':'   ')+item.name+(item.dir?'/':''),item.dir?'info':'out'));
      if (!visible.length) emit('(empty)','dim'); pipe=bash?visible.map(n=>n.name):visible.map(n=>({Name:n.name,Mode:n.dir?'d-----':'-a----',Length:n.data.length}));
    }
    else if (['mkdir','new-item','touch'].includes(cmd)) {
      const names=positional.length?positional:[pathArg];
      for (const p of names) { const dest=this.resolve(session,p), isDir=cmd!=='touch'&&!args.some(x=>x.toLowerCase()==='-itemtype'&&args[args.indexOf(x)+1]?.toLowerCase()==='file');
        if (this.node(session,dest)) { if (isDir&&!args.includes('-p')&&!args.includes('--parents')) throw new Error(`Already exists: ${p}`); continue; }
        if(isDir&&(args.includes('-p')||args.includes('--parents'))){const segments=dest.split('/').filter(Boolean);let cursor=dest.startsWith('/')?'/':segments.shift()+':';for(const segment of segments){cursor=cursor==='/'?`/${segment}`:`${cursor.replace(/\/$/,'')}/${segment}`;if(!this.node(session,cursor))this.put(session,cursor,'',true);}}
        else this.put(session,dest,'',isDir);
      }
    }
    else if (['echo','write-output','write-host','printf'].includes(cmd)) {
      let value=args.join(' ').replace(/\\n/g,'\n');value=value.replace(/\$env:([\w]+)/gi,(_,k)=>s.env[k.toUpperCase()]??'').replace(/\$\{?([\w]+)\}?/g,(_,k)=>s.vars[k]??s.env[k]??'');
      if(cmd==='printf'){for(let i=1;i<args.length;i++)value=value.replace(/%([sdif])/,(m,f)=>f==='f'?String(Number(args[i])):args[i]);}
      value.split('\n').forEach(v=>emit(v)); pipe=value.split('\n');
    }
    else if(['write-warning','write-error'].includes(cmd)){const value=args.join(' ');emit(value,cmd==='write-error'?'err':'warn');if(cmd==='write-error')code=1;}
    else if(cmd==='get-itemproperty'){emit('Get-ItemProperty: Windows registry access is not modeled in the virtual environment.','warn');code=1;}
    else if(cmd==='get-item'){
      if(pathArg.toLowerCase().startsWith('env:')){const key=pathArg.slice(4).toUpperCase();if(!key){pipe=Object.entries(s.env).map(([Name,Value])=>({Name,Value}));}else if(Object.hasOwn(s.env,key))pipe=[{Name:key,Value:s.env[key]}];else throw new Error(`Environment variable not found: ${key}`);pipe.forEach(item=>Object.entries(item).forEach(([name,value])=>emit(`${name}: ${value}`)));}
      else {const target=this.resolve(session,pathArg),item=this.node(session,target);if(!item)throw new Error(`Path not found: ${pathArg}`);const object={FullName:target,Name:item.name,PSIsContainer:!!item.dir,Length:item.dir?0:item.data.length,LastWriteTime:item.modified};Object.entries(object).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[object];}
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
      for (const p of positional) {if(!this.node(session,p)&&bash&&args.some(flag=>['-f','-rf','-fr','-rF','-Rf'].includes(flag)))continue;this.remove(session,p,args.includes('-Recurse')||args.includes('-r')||args.includes('-R')||args.includes('-rf')||args.includes('-fr')||args.includes('-Force')&&args.includes('-Recurse'));}
    }
    else if (['cp','copy-item','copy'].includes(cmd)||['mv','move-item','move'].includes(cmd)) {
      if (positional.length<2) throw new Error('Expected a source and destination.');
      const [from,to]=positional.slice(-2), src=this.node(session,from); if (!src) throw new Error(`Source not found: ${from}`);
      if(src.dir&&bash&&['cp','copy'].includes(cmd)&&!args.some(flag=>['-r','-R','-a'].includes(flag)))throw new Error(`cp: -r not specified; omitting directory '${from}'`);
      const destNode=this.node(session,to), dest=this.resolve(session,destNode?.dir?posix.join(this.resolve(session,to),src.name):to);
      if (src.dir) { const descendants=Object.entries(s.fs).filter(([k])=>k===this.key(session,this.resolve(session,from))||k.startsWith(this.key(session,this.resolve(session,from)).replace(/\/$/,'')+'/')); for (const [k,n] of descendants) { const rel=k.slice(this.key(session,this.resolve(session,from)).length); s.fs[this.key(session,dest+rel)]={...n,name:posix.basename(dest+rel),modified:now()}; } }
      else this.put(session,dest,src.data,false,src.executable);
      if (['mv','move-item','move'].includes(cmd)) this.remove(session,from,true);
    }
    else if (cmd==='grep'&&bash) {
      const options=new Set(args.filter(value=>value.startsWith('-'))),short=new Set([...options].filter(value=>!value.startsWith('--')).flatMap(value=>[...value.slice(1)]));
      const expressionAt=args.indexOf('-e'),pattern=expressionAt>=0?args[expressionAt+1]:positional[0],paths=expressionAt>=0?positional.filter(value=>value!==pattern):positional.slice(1);
      if(!pattern){emit('grep: missing search pattern','err');code=2;}
      else {
        const sources=[];if(pipeInput)sources.push({path:null,lines:pipeInput.map(String)});
        else for(const value of paths){const target=this.resolve(session,value),node=this.node(session,target);if(node?.dir&&(short.has('r')||short.has('R')||options.has('--recursive'))){for(const [key,file]of Object.entries(s.fs)){if(key.startsWith(target.replace(/\/$/,'')+'/')&&!file.dir)sources.push({path:key,lines:file.data.split(/\r?\n/)});}continue;}if(!node||node.dir){emit(`grep: ${value}: ${node?'Is a directory':'No such file or directory'}`,'err');continue;}sources.push({path:value,lines:node.data.split(/\r?\n/)});}
        let regex;try{let source=short.has('F')?String(pattern).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'):pattern;if(short.has('w'))source=`\\b(?:${source})\\b`;regex=new RegExp(source,short.has('i')?'i':'');}catch{regex=new RegExp(String(pattern).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),short.has('i')?'i':'');}
        const results=[];for(const item of sources){let count=0;for(let index=0;index<item.lines.length;index++){const value=String(item.lines[index]),found=regex.test(value)!==short.has('v');regex.lastIndex=0;if(!found)continue;count++;if(short.has('q')||short.has('l')||short.has('c'))continue;if(short.has('o')){const matches=[...value.matchAll(new RegExp(regex.source,regex.flags.includes('g')?regex.flags:`${regex.flags}g`))].map(match=>match[0]);for(const match of matches)results.push(match);continue;}const prefix=[];if(sources.length>1&&item.path)prefix.push(item.path);if(short.has('n'))prefix.push(String(index+1));results.push(`${prefix.length?prefix.join(':')+':':''}${value}`);}if(short.has('c')&&item.path)results.push(`${item.path}:${count}`);else if(short.has('c'))results.push(String(count));else if(short.has('l')&&count&&item.path)results.push(item.path);}
        if(!short.has('q'))results.forEach(value=>emit(value));pipe=results;code=results.length?0:1;
      }
    }
    else if (['select-string','findstr'].includes(cmd)) {
      const pattern=positional[0]||'', inputs=pipeInput||positional.slice(1).flatMap(p=>{const n=this.node(session,p); if (!n||n.dir) return []; return n.data.split(/\r?\n/);});
      const matched=inputs.filter(value=>args.some(x=>x.toLowerCase()==='-caseinsensitive'||x.toLowerCase()==='/i')?String(value).toLowerCase().includes(pattern.toLowerCase()):String(value).includes(pattern));matched.forEach(value=>emit(value));pipe=matched;code=matched.length?0:1;
    }
    else if (cmd==='find') {
      const rootValue=positional.find(value=>!['f','d'].includes(value))||'.',root=this.resolve(session,rootValue),node=this.node(session,root),nameAt=args.indexOf('-name'),typeAt=args.indexOf('-type'),pattern=nameAt>=0?args[nameAt+1]:'*',wantedType=typeAt>=0?args[typeAt+1]:null;
      if(!node?.dir)throw new Error(`find: '${rootValue}': No such file or directory`);
      const entries=[[root,node],...Object.entries(s.fs).filter(([key])=>key.startsWith(root.replace(/\/$/,'')+'/'))],regex=new RegExp('^'+String(pattern).replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$');
      for(const [key,item]of entries){if(!item||wantedType==='f'&&item.dir||wantedType==='d'&&!item.dir||!regex.test(item.name))continue;const outputPath=rootValue==='.'&&key===root?'.':key;emit(outputPath);if(!pipe)pipe=[];pipe.push(outputPath);}
    }
    else if (['head','tail','wc'].includes(cmd)) {
      const countAt=args.findIndex(value=>value==='-n'),nlines=Math.max(0,Number(countAt>=0?args[countAt+1]:args.find(x=>/^\d+$/.test(x))||10)),paths=args.filter((value,index)=>!value.startsWith('-')&&!(countAt>=0&&index===countAt+1)),file=paths[0]?this.node(session,paths[0]):null;
      if(paths[0]&&(!file||file.dir))throw new Error(`${cmd}: ${paths[0]}: No such file or directory`);
      const lines=pipeInput||(file?(file.data.replace(/\r?\n$/,'')?file.data.replace(/\r?\n$/,'').split(/\r?\n/):[]):[]);
      let result;if(cmd==='head')result=lines.slice(0,nlines);else if(cmd==='tail')result=nlines?lines.slice(-nlines):[];else{const options=new Set(args.filter(value=>value.startsWith('-'))),lineCount=lines.length,wordCount=lines.reduce((count,value)=>count+String(value).trim().split(/\s+/).filter(Boolean).length,0),byteCount=lines.reduce((count,value)=>count+Buffer.byteLength(String(value),'utf8')+1,0);result=options.has('-l')?[String(lineCount)]:options.has('-w')?[String(wordCount)]:options.has('-c')?[String(byteCount)]:[`${lineCount} ${wordCount} ${byteCount}`];}
      result.forEach(v=>emit(v)); pipe=result;
    }
    else if(bash&&pipeInput!==null&&['sort','uniq','tr','out-null'].includes(cmd)){
      const values=pipeInput.map(String);
      if(cmd==='sort'){const insensitive=args.includes('-f'),descending=args.includes('-r');pipe=[...values].sort((a,b)=>{const left=insensitive?a.toLowerCase():a,right=insensitive?b.toLowerCase():b;return left<right?-1:left>right?1:0;});if(descending)pipe.reverse();}
      else if(cmd==='uniq'){pipe=values.filter((value,index)=>index===0||value!==values[index-1]);}
      else if(cmd==='tr')pipe=values;
      else pipe=[];
      if(cmd!=='out-null')pipe.forEach(value=>emit(value));
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
    else if (['ps','top','get-process','gps'].includes(cmd)) {
      let processes=Object.entries(s.processes).map(([pid,processName])=>({Id:Number(pid),PID:Number(pid),Name:processName,CPU:pid==='4'?12.5:1.2,WorkingSet:pid==='4'?10485760:41943040,State:'Running'}));
      const nameAt=args.findIndex(x=>x.toLowerCase()==='-name'),name=nameAt>=0?args[nameAt+1]:cmd==='get-process'||cmd==='gps'?positional.find(x=>!x.startsWith('-')):null;
      const pattern=name?new RegExp('^'+name.replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$','i'):null;if(pattern)processes=processes.filter(item=>pattern.test(item.Name));
      if(cmd==='get-process'||cmd==='gps')processes.forEach(item=>{emit(`Id: ${item.Id}`);emit(`Name: ${item.Name}`);emit(`CPU: ${item.CPU}`);emit(`WorkingSet: ${item.WorkingSet}`);});
      else {emit('USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND');processes.forEach(item=>emit(`root      ${String(item.PID).padStart(4)}  0.0  0.2  41940 10240 ?        Ss   06:01   0:00 ${item.Name}`));}
      pipe=processes;
    }
    else if(cmd==='start-process'){
      const fileAt=args.findIndex(x=>x.toLowerCase()==='-filepath'),rawName=fileAt>=0?args[fileAt+1]:positional[0];if(!rawName)throw new Error('Start-Process requires a virtual program name.');
      const allowed=new Set(['notepad','notepad.exe','calc','calc.exe','explorer','explorer.exe','code','code.exe','python','python.exe','terminal']),processName=posix.basename(String(rawName).replaceAll('\\','/')).toLowerCase();if(!allowed.has(processName))throw new Error(`Start-Process: '${rawName}' is not in the virtual application catalog.`);
      if(args.some(x=>x.toLowerCase()==='-whatif'))emit(`What if: Start virtual process ${processName}.`,'dim');
      else {const pid=String(Math.max(1000,...Object.keys(s.processes).map(Number))+1),nameWithoutExtension=processName.replace(/\.exe$/i,'');s.processes[pid]=nameWithoutExtension;if(args.some(x=>x.toLowerCase()==='-passthru')){emit(`Id: ${pid}`);emit(`Name: ${nameWithoutExtension}`);pipe=[{Id:Number(pid),Name:nameWithoutExtension}];}else emit(`Started virtual process ${processName} (PID ${pid}).`,'ok');}
    }
    else if(cmd==='stop-process'){
      const idAt=args.findIndex(x=>x.toLowerCase()==='-id'),nameAt=args.findIndex(x=>x.toLowerCase()==='-name'),pid=idAt>=0?args[idAt+1]:args.find(x=>/^\d+$/.test(x)),name=nameAt>=0?args[nameAt+1]:positional.find(x=>!/^\d+$/.test(x));
      const piped=pipeInput?.map(value=>value&&typeof value==='object'?value.Id??value.PID:value).filter(value=>value!==undefined)||[],targets=Object.keys(s.processes).filter(key=>pid?key===String(pid):name?s.processes[key].toLowerCase()===String(name).toLowerCase():piped.some(value=>String(value)===key));
      if(!targets.length)throw new Error('Stop-Process: no matching virtual process was found.');if(targets.some(key=>['4','2844'].includes(key)))throw new Error('Stop-Process: protected simulator and system processes cannot be stopped.');
      if(args.some(x=>x.toLowerCase()==='-whatif'))emit(`What if: Stop virtual processes ${targets.join(', ')}.`,'dim');else{targets.forEach(key=>delete s.processes[key]);emit('The simulated process was stopped.','ok');}
    }
    else if(['kill','pkill'].includes(cmd)){const target=args.find(x=>/^\d+$/.test(x))||positional.at(-1);const matches=Object.keys(s.processes).filter(pid=>cmd==='kill'?pid===target:s.processes[pid].toLowerCase().includes(String(target||'').toLowerCase()));if(!matches.length)throw new Error(`No virtual process matched ${target||''}.`);matches.forEach(pid=>delete s.processes[pid]);}
    else if (['systemctl','service','get-service','start-service','stop-service','restart-service'].includes(cmd)) {
      const serviceLabels={BITS:'Background Intelligent Transfer Service',EventLog:'Windows Event Log',Spooler:'Print Spooler',wuauserv:'Windows Update',ssh:'OpenSSH server (virtual)',cron:'Cron scheduler (virtual)',nginx:'Nginx web server (virtual)',docker:'Docker service (virtual)'};
      const nameAt=args.findIndex(x=>x.toLowerCase()==='-name'),action=cmd==='systemctl'?args[0]:cmd==='service'?args[1]:null;
      if(cmd==='get-service'||action==='list-units'||args.includes('--type=service')){
        const query=nameAt>=0?args[nameAt+1]:cmd==='get-service'?positional[0]:'*',patterns=(pipeInput||[]).map(item=>item&&typeof item==='object'?item.Name:null).filter(Boolean),selected=patterns.length&&cmd!=='get-service'?patterns:[query||'*'];
        const entries=Object.entries(s.services).filter(([key])=>selected.some(pattern=>{const regex=new RegExp('^'+String(pattern).replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$','i');return regex.test(key)||regex.test(serviceLabels[key]||key);})).map(([Name,status])=>({Name,Status:status==='running'?'Running':'Stopped',DisplayName:serviceLabels[Name]||Name,StartType:'Manual'}));
        entries.forEach(item=>emit(`${item.Status.padEnd(8)} ${item.Name.padEnd(18)} ${item.DisplayName}`));if(!entries.length)emit('(no matching services)','dim');pipe=entries;code=entries.length?0:1;
      } else {
        const serviceAction=cmd==='start-service'?'start':cmd==='stop-service'?'stop':cmd==='restart-service'?'restart':action,requested=nameAt>=0?[args[nameAt+1]]:cmd==='systemctl'?[args[1]]:cmd==='service'?[args[0]]:positional.length?[positional[0]]:(pipeInput||[]).map(item=>item&&typeof item==='object'?item.Name:null).filter(Boolean),patterns=(requested||[]).filter(Boolean),targets=Object.keys(s.services).filter(key=>patterns.some(pattern=>new RegExp('^'+String(pattern).replace(/[.+^${}()|[\]\\]/g,'\\$&').replaceAll('*','.*').replaceAll('?','.')+'$','i').test(key)));
        if(!targets.length)throw new Error(`Service not found in the virtual system: ${(patterns||[]).join(', ')}`);
        if(args.some(x=>x.toLowerCase()==='-whatif'))emit(`What if: Change virtual service ${targets.join(', ')}.`,'dim');else for(const key of targets){if(['start','restart'].includes(serviceAction))s.services[key]='running';else if(serviceAction==='stop')s.services[key]='stopped';emit(`${key}.service - ${s.services[key]}`,'ok');}
      }
    }
    else if (cmd==='df') { emit('Filesystem      Size  Used Avail Use% Mounted on');const used=Object.values(s.fs).reduce((v,n)=>v+(n.data||'').length,0);emit(`virtualfs       10G   ${Math.ceil(used/1048576)}M    10G   1% /`); }
    else if (cmd==='du') { const n=this.node(session,pathArg);if(!n)throw new Error(`No such path: ${pathArg}`);const prefix=this.key(session,this.resolve(session,pathArg)).replace(/\/$/,'')+'/';const size=Object.entries(s.fs).filter(([k])=>k===this.key(session,this.resolve(session,pathArg))||k.startsWith(prefix)).reduce((v,[,x])=>v+(x.data||'').length,0);emit(`${Math.ceil(size/1024)}\t${pathArg}`); }
    else if (cmd==='free') { emit('              total        used        free      shared  buff/cache   available');emit('Mem:           7.7Gi       1.2Gi       5.1Gi        32Mi       1.4Gi       6.2Gi');emit('Swap:          2.0Gi          0B       2.0Gi'); }
    else if (cmd==='uptime') emit('up 2 days, 4:18, 1 user, load average: 0.08, 0.05, 0.02');
    else if(['get-netipaddress','ipconfig'].includes(cmd)){
      if(cmd==='ipconfig'&&args.some(x=>x.toLowerCase()==='/flushdns'))emit('Successfully flushed the virtual DNS resolver cache.','ok');
      else if(cmd==='ipconfig'){emit('Windows IP Configuration');emit('Ethernet adapter Virtual Sandbox:');emit('   IPv4 Address. . . . . . . . . . . : 192.0.2.10');emit('   Default Gateway . . . . . . . . . : 192.0.2.1');}
      else {const item={InterfaceAlias:'Virtual Ethernet',IPAddress:'192.0.2.10',AddressFamily:'IPv4',PrefixLength:24,Mode:'virtual/offline'};Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[item];}
    }
    else if(['get-nettcpconnection','netstat'].includes(cmd)){
      if(cmd==='netstat'){emit('Active Connections');emit('  Proto  Local Address          Foreign Address        State           PID');emit('  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       912');}
      else {const stateAt=args.findIndex(x=>x.toLowerCase()==='-state'),wanted=stateAt>=0?args[stateAt+1]:null;const items=[{LocalAddress:'127.0.0.1',LocalPort:8080,State:'Listen',OwningProcess:912,Mode:'virtual/offline'}].filter(item=>!wanted||item.State.toLowerCase()===wanted.toLowerCase());items.forEach(item=>Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`)));pipe=items;}
    }
    else if(['get-volume','get-disk','fdisk','lsblk'].includes(cmd)){
      if(cmd==='get-volume'){const item={DriveLetter:'C',FileSystemLabel:'OmniTerm',FileSystem:'VirtualFS',SizeRemaining:8*1024**3,Size:10*1024**3};Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[item];}
      else if(cmd==='get-disk'){const item={Number:0,FriendlyName:'OmniTerm Virtual Disk',OperationalStatus:'Online',PartitionStyle:'GPT',Size:64*1024**3};Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[item];}
      else {emit('Number FriendlyName       Size       HealthStatus');emit('0      Virtual Disk       64 GB      Healthy');emit('Device      Start       End   Sectors  Size Type');emit('/dev/vda1    2048 134217727 134215680   64G Linux filesystem');}
    }
    else if(cmd==='test-netconnection'){
      const hostAt=args.findIndex(x=>x.toLowerCase()==='-computername'),portAt=args.findIndex(x=>x.toLowerCase()==='-port');const host=hostAt>=0?args[hostAt+1]:positional[0]||'virtual-host',port=portAt>=0?args[portAt+1]:'';const item={ComputerName:host,RemotePort:port,TcpTestSucceeded:false,Mode:'virtual/offline'};Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[item];code=1;
    }
    else if(['get-simulatednetwork','set-simulatednetwork'].includes(cmd)){
      if(cmd==='set-simulatednetwork')for(const [key,field,min,max] of [['latencyms','networkLatency',1,5000],['losspercent','networkLoss',0,100]]){const at=args.findIndex(x=>x.toLowerCase()===`-${key}`);const inline=args.find(x=>x.toLowerCase().startsWith(`-${key}:`));const raw=at>=0?args[at+1]:inline?.slice(inline.indexOf(':')+1);if(raw!==undefined){const value=Number(raw);if(!Number.isFinite(value))throw new Error(`-${key} must be numeric.`);s[field]=Math.max(min,Math.min(max,value));}}
      const item={LatencyMs:s.networkLatency,LossPercent:s.networkLoss,Mode:'virtual/offline'};Object.entries(item).forEach(([key,value])=>emit(`${key}: ${value}`));pipe=[item];
    }
    else if(cmd==='robocopy'){
      const paths=args.filter(value=>!value.startsWith('/'));if(paths.length<2)throw new Error('ROBOCOPY: source and destination directories are required.');const source=this.resolve(session,paths[0]),destination=this.resolve(session,paths[1]),sourceNode=this.node(session,source),destinationNode=this.node(session,destination);
      if(!sourceNode?.dir)throw new Error('ROBOCOPY: source must be a virtual directory.');if(destinationNode&&!destinationNode.dir)throw new Error('ROBOCOPY: destination must be a virtual directory.');if(!destinationNode)this.put(session,destination,'',true);
      let copied=0;const pending=[source];while(pending.length){const parent=pending.pop();for(const child of this.children(session,parent)){const childPath=posix.join(parent,child.name),relative=posix.relative(source,childPath),target=posix.join(destination,relative);if(child.dir){if(!this.node(session,target))this.put(session,target,'',true);pending.push(childPath);}else{this.put(session,target,child.data,false,child.executable);copied++;}}}
      emit(`Simulated copy complete: ${copied} file(s) copied inside the virtual filesystem.`);code=copied?1:0;
    }
    else if(cmd==='net'){
      const subcommand=(args[0]||'').toLowerCase();if(subcommand==='user'){emit('User accounts for \\\\DESKTOP-LAB');emit('-------------------------------------------------------------------------------');emit('admin                    DefaultAccount           Guest');emit('The command completed successfully. (simulated)');}
      else if(subcommand==='localgroup'){emit('Alias name     administrators');emit('Members        *admin');emit('The command completed successfully. (simulated)');}
      else if(subcommand==='share'){emit('Share name   Resource          Remark');emit('IPC$         (virtual)         Remote IPC');emit('The command completed successfully. (simulated)');}
      else {emit('The NET command is supported for virtual user, localgroup, and share queries.','warn');code=1;}
    }
    else if(['systeminfo','systemd-analyze'].includes(cmd)){emit('Host Name:                 DESKTOP-LAB');emit('OS Name:                   Microsoft Windows 11 Pro');emit('OS Version:                10.0.22631 N/A Build 22631');emit('System Type:               x64-based PC');}
    else if(['netsh','reg','certutil','bitsadmin','nltest','vssadmin','wbadmin','chkdsk','sfc','dism','wmic','qwinsta','quser'].includes(cmd)){
      if(cmd==='chkdsk')emit('The virtual filesystem was checked. No errors were found. (simulated)','ok');
      else if(cmd==='sfc')emit('Windows Resource Protection did not find any integrity violations. (simulated)','ok');
      else if(cmd==='dism')emit('The virtual component store is healthy. (simulated)','ok');
      else if(cmd==='reg'&&args[0]?.toLowerCase()==='query'){emit('HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer');emit('    AlwaysInstallElevated    REG_DWORD    0x0 (simulated)');}
      else if(cmd==='reg'){emit('reg: only read-only simulated query is supported.','warn');code=126;}
      else if(cmd==='netsh')emit('Host network and firewall settings cannot be changed from the isolated simulator.','warn');
      else if(cmd==='certutil')emit('CertUtil: host certificates and files are not accessible in the virtual environment.','warn');
      else if(cmd==='bitsadmin')emit('BITS transfer jobs are virtual only; no network request was made.','warn');
      else if(cmd==='nltest')emit('Domain controller discovery is unavailable in the offline virtual environment.','warn');
      else if(cmd==='vssadmin'){emit('Contents of shadow copy set ID: {virtual-snapshot}');emit('   Shadow Copy ID: {simulated}');emit('   Original Volume: (C:)\\\\?\\Volume{virtual}\\ (simulated)');}
      else if(cmd==='wbadmin')emit('Backup versions: 1 simulated virtual backup version; no host backups are visible.');
      else if(cmd==='wmic'){
        const query=args.join(' ').toLowerCase();if(query.includes('process')){emit('Name                 ExecutablePath                 ProcessId');for(const [pid,processName]of Object.entries(s.processes))emit(`${processName.padEnd(20)} C:\\Windows\\System32\\${processName}.exe ${pid}`);}
        else if(query.includes('qfe')){emit('Caption  Description  HotFixID  InstalledOn');emit('Security Update  Security Update  KB503xxxx  2026-10-01 (simulated)');}
        else if(query.includes('service')){emit('Name       DisplayName       PathName                         StartMode');emit('OmniDemo   OmniTerm Demo    C:\\Windows\\System32\\svchost.exe Auto');}
        else emit('wmic: simulated query; host inventory is not accessible.','info');
      }
      else {emit(' SESSIONNAME       USERNAME        ID  STATE   TYPE        DEVICE');emit('>console           admin            1  Active  wdcon');}
      if(['netsh','certutil','bitsadmin','nltest'].includes(cmd))code=126;
    }
    else if(['lsblk','lsof','ss','crontab','dmesg','nano','screen','tmux'].includes(cmd)){
      if(cmd==='lsblk'){emit('NAME   MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS');emit('vda    253:0    0   64G  0 disk /');emit('vda1   253:1    0   64G  0 part /');}
      else if(['lsof','ss'].includes(cmd)){emit('State   Recv-Q Send-Q Local Address:Port  Peer Address:Port Process');emit('LISTEN  0      128    127.0.0.1:8080       0.0.0.0:*       virtual-service');}
      else if(cmd==='crontab'){emit(args.includes('-e')?'crontab: interactive editor is unavailable; scheduled tasks are not changed.':'# simulated crontab for root',args.includes('-e')?'warn':'out');if(!args.includes('-e'))emit('0 * * * * /usr/local/bin/virtual-maintenance');}
      else if(cmd==='dmesg'){emit('[    0.000000] Linux version 6.8.0-45-generic (virtual sandbox)');emit('[    0.120000] OmniTerm virtual filesystem initialized');}
      else emit(`${name}: interactive host applications are unavailable in the simulator.`,'warn');
    }
    else if(['tasklist','taskkill'].includes(cmd)){if(cmd==='taskkill'){const pidAt=args.findIndex(x=>x.toLowerCase()==='/pid'),imageAt=args.findIndex(x=>x.toLowerCase()==='/im'),pid=pidAt>=0?args[pidAt+1]:null,image=imageAt>=0?args[imageAt+1]?.replace(/\.exe$/i,'').toLowerCase():null;const matches=Object.keys(s.processes).filter(key=>pid?key===pid:image?s.processes[key].toLowerCase()===image:false);if(!matches.length)throw new Error('No simulated process matched the request.');matches.forEach(key=>delete s.processes[key]);emit('SUCCESS: The simulated process was terminated.','ok');}else{emit('Image Name                     PID Session Name');Object.entries(s.processes).forEach(([pid,name])=>emit(`${name.padEnd(30)} ${pid} Console`));}}
    else if(['route','arp'].includes(cmd)){emit(cmd==='route'?'IPv4 Route Table':'Interface: 192.0.2.10 --- 0x7');emit(cmd==='route'?'Network Destination        Netmask          Gateway       Interface  Metric':'  Internet Address      Physical Address      Type');emit(cmd==='route'?'0.0.0.0                    0.0.0.0          192.0.2.1     192.0.2.10     25':'  192.0.2.1             00-00-5e-00-53-01     dynamic');}
    else if (['ping','test-connection'].includes(cmd)) {
      const countAt=args.findIndex(x=>['-n','-c','-count'].includes(x.toLowerCase())),computerAt=args.findIndex(x=>x.toLowerCase()==='-computername'),target=computerAt>=0?args[computerAt+1]:positional.find(x=>!/^\d+$/.test(x)),count=Number(countAt>=0?args[countAt+1]:4);
      if(!target){emit(`${name}: specify a virtual host name.`,'err');code=1;}
      else if(['unreachable.invalid','no-such-host.invalid'].includes(target.toLowerCase())){emit(`Ping request could not find host ${target}. Check the name and try again.`,'err');code=1;}
      else {const total=Math.max(1,Math.min(10,Number.isFinite(count)?Math.floor(count):4));let received=0;for(let i=0;i<total;i++){if(Math.random()*100<s.networkLoss){emit('Request timed out.','warn');continue;}received++;const latency=Math.max(1,Math.round(s.networkLatency*(0.88+Math.random()*0.24)));emit(session.shell==='ps'?`Reply from 203.0.113.10: bytes=32 time=${latency}ms TTL=57`:`64 bytes from ${target}: icmp_seq=${i+1} ttl=57 time=${latency} ms`);}const lost=total-received,loss=Math.floor(lost*100/total);emit(`Ping statistics for ${target}: Packets: Sent = ${total}, Received = ${received}, Lost = ${lost} (${loss}% loss)`,'dim');code=received?0:1;}
      pipe=out.map(item=>item.text);
    }
    else if (['apt','apt-get','apt-cache','dpkg','winget','pip','pip3','choco'].includes(cmd)||cmd==='python'&&args[0]==='-m'&&args[1]==='pip') {const result=this.packageCommand(session,cmd,args);out.push(...result.output);code=result.code;pipe=result.pipe;}
    else if (cmd==='git') {
      const action=(args[0]||'status').toLowerCase(),values=args.slice(1);
      const rootFor=()=>Object.keys(s.git).filter(root=>{const cwd=this.resolve(session,s.cwd),normalized=this.key(session,root);return this.key(session,cwd)===normalized||this.key(session,cwd).startsWith(normalized.replace(/\/$/,'')+'/');}).sort((a,b)=>b.length-a.length)[0];
      const currentRepo=()=>{const root=rootFor();return root?{root,repo:s.git[this.key(session,root)]}:null;};
      if(action==='--version'){emit('git version 2.47.0');}
      else if(action==='init'){
        const root=this.resolve(session,values.find(x=>!x.startsWith('-'))||'.');const parent=this.node(session,posix.dirname(root));if(!parent?.dir)throw new Error('fatal: repository parent does not exist');if(!this.node(session,root))this.put(session,root,'',true);
        this.put(session,posix.join(root,'.git'),'{}',true);s.git[this.key(session,root)]={root,origin:'',branch:'main',branches:['main'],commits:[],staged:[],tracked:[],trackedData:{},tags:[]};emit(`Initialized empty virtual Git repository in ${root}/.git/`,'ok');
      }
      else if(action==='clone'){
        const branchAt=values.findIndex(x=>x==='-b'||x==='--branch'),branch=branchAt>=0?values[branchAt+1]:'main',parts=values.filter((x,i)=>!x.startsWith('-')&&i!==branchAt+1),origin=parts[0],destination=parts[1]||origin?.split('/').at(-1)?.replace(/\.git$/i,'');
        if(!origin||!destination)throw new Error('fatal: repository URL is required');let parsed;try{parsed=new URL(origin.replace(/^git@github\.com:/i,'https://github.com/'));}catch{throw new Error('fatal: unsupported virtual repository URL');}
        if(parsed.hostname.toLowerCase()!=='github.com')throw new Error('fatal: only GitHub-shaped repository addresses are simulated offline');
        const root=this.resolve(session,destination),parent=this.node(session,posix.dirname(root));if(!parent?.dir)throw new Error('fatal: destination parent does not exist');if(this.node(session,root))throw new Error(`fatal: destination path '${destination}' already exists`);
        const pathParts=parsed.pathname.replace(/^\//,'').replace(/\.git$/i,'').split('/'),repository=pathParts.at(-1)||'repository';
        this.put(session,root,'',true);this.put(session,posix.join(root,'.git'),'{}',true);this.put(session,posix.join(root,'README.md'),`# ${repository}\n\nVirtual repository metadata. No code was fetched or executed.\n`);
        const readme=this.node(session,posix.join(root,'README.md')).data;s.git[this.key(session,root)]={root,origin,branch,branches:[branch],commits:[{id:'a1b2c3d',message:'simulated initial commit'}],staged:[],tracked:['README.md'],trackedData:{'README.md':readme},tags:[]};
        emit(`Cloning into '${destination}'...`);emit('remote: Repository metadata response (simulated; no network request was made)','dim');emit('Repository created in virtual file system; no repository code was run.','ok');
      }
      else if(['push','pull','fetch'].includes(action)){emit('fatal: remote access is disabled; repository operations are simulated offline.','err');code=1;}
      else {
        const found=currentRepo();if(!found)throw new Error('fatal: not a git repository (or any parent directory): .git');const {root,repo}=found;
        if(action==='status'){
          const tracked=new Set(repo.tracked||[]),staged=new Set(repo.staged||[]),changed=[],deleted=[],untracked=[];
          for(const relative of tracked){const file=this.node(session,posix.join(root,relative));if(!file)deleted.push(relative);else if(file.data!==(repo.trackedData||{})[relative])changed.push(relative);}
          for(const key of Object.keys(s.fs)){if(!key.startsWith(this.key(session,root).replace(/\/$/,'')+'/'))continue;const relative=key.slice(this.key(session,root).length).replace(/^\//,'');if(relative.startsWith('.git/')||relative==='.git'||tracked.has(relative)||staged.has(relative))continue;if(!s.fs[key].dir)untracked.push(relative);}
          emit(`On branch ${repo.branch}`);if(repo.commits?.length)emit('Your branch is up to date with the simulated origin.','dim');
          if(staged.size){emit('Changes to be committed:','head');for(const file of staged)emit(`  staged: ${file}`,'ok');}
          if(changed.length||deleted.length){emit('Changes not staged for commit:','head');for(const file of changed)emit(`  modified: ${file}`,'warn');for(const file of deleted)emit(`  deleted: ${file}`,'err');}
          if(untracked.length){emit('Untracked files:','head');for(const file of untracked)emit(`  ${file}`,'warn');}
          if(!staged.size&&!changed.length&&!deleted.length&&!untracked.length)emit('nothing to commit, working tree clean','dim');
        }
        else if(action==='branch'){
          const branch=values.find(x=>!x.startsWith('-'));
          if(values.includes('-D')||values.includes('-d')){if(!branch)throw new Error('git branch: branch name required');if(branch===repo.branch)throw new Error(`Cannot delete branch '${branch}' checked out at current worktree`);repo.branches=repo.branches.filter(x=>x!==branch);}
          else if(branch){if(repo.branches.includes(branch))throw new Error(`fatal: a branch named '${branch}' already exists`);repo.branches.push(branch);}
          else for(const name of repo.branches||[repo.branch])emit(`${name===repo.branch?'*':' '} ${name}`,name===repo.branch?'ok':'out');
        }
        else if(['checkout','switch'].includes(action)){
          const create=values.includes('-b')||values.includes('-c'),name=values.find(x=>!x.startsWith('-'));if(!name)throw new Error(`git ${action}: branch name required`);
          if(create){if(repo.branches.includes(name))throw new Error(`fatal: a branch named '${name}' already exists`);repo.branches.push(name);}
          else if(!repo.branches.includes(name))throw new Error(`error: pathspec '${name}' did not match any virtual branch`);
          repo.branch=name;emit(`Switched to ${create?'a new ':''}branch '${name}'`,'ok');
        }
        else if(action==='log'){
          for(const commit of [...(repo.commits||[])].reverse()){emit(`commit ${commit.id}`,'head');emit(`    ${commit.message}`);}
        }
        else if(action==='add'){
          const targets=values.length?values:['.'];for(const value of targets){if(value==='.'||value==='-A'||value==='--all'){for(const key of Object.keys(s.fs))if(key.startsWith(this.key(session,root).replace(/\/$/,'')+'/')){const relative=key.slice(this.key(session,root).length).replace(/^\//,'');if(!relative.startsWith('.git/')&&!s.fs[key].dir&&!repo.staged.includes(relative))repo.staged.push(relative);}}else{const resolved=this.resolve(session,posix.join(root,value)),file=this.node(session,resolved);if(!file)throw new Error(`pathspec '${value}' did not match any files`);const relative=posix.relative(root,resolved);if(!repo.staged.includes(relative))repo.staged.push(relative);}}
          emit('Changes staged in virtual index.','ok');
        }
        else if(action==='commit'){
          if(!repo.staged.length)throw new Error('nothing to commit, working tree clean');const messageAt=values.indexOf('-m'),message=messageAt>=0?values.slice(messageAt+1).join(' '):'virtual commit';const commit={id:id().slice(0,8),message:message||'virtual commit'};repo.commits.push(commit);
          for(const relative of repo.staged){const file=this.node(session,posix.join(root,relative));if(file){repo.trackedData[relative]=file.data;if(!repo.tracked.includes(relative))repo.tracked.push(relative);}else{delete repo.trackedData[relative];repo.tracked=repo.tracked.filter(x=>x!==relative);}}
          repo.staged=[];emit(`[${repo.branch} ${commit.id}] ${commit.message}`,'ok');
        }
        else if(action==='remote'){
          if(!repo.origin)emit('No remotes configured.','dim');else{emit(`origin\t${repo.origin} (fetch)`);emit(`origin\t${repo.origin} (push)`);}
        }
        else if(action==='tag'){
          const tag=values.find(x=>!x.startsWith('-'));repo.tags=repo.tags||[];if(tag){if(repo.tags.includes(tag))throw new Error(`fatal: tag '${tag}' already exists`);repo.tags.push(tag);emit(`Created virtual tag '${tag}'.`,'ok');}else repo.tags.forEach(x=>emit(x));
        }
        else if(action==='show'){
          const file=values.find(x=>!x.startsWith('-')),target=file?this.node(session,posix.join(root,file)):null;if(target&&!target.dir)target.data.split(/\r?\n/).forEach(row=>emit(row));else emit(`commit ${repo.commits.at(-1)?.id||'no commits'}\n    ${repo.commits.at(-1)?.message||'virtual repository'}`);
        }
        else emit(`git: '${action}' is not a supported virtual git command.`,'err');
      }
    }
    else if (cmd==='docker'||cmd==='docker-compose') {const action=args[0]||'';if(action==='--version')emit('Docker version 27.0.0, build virtual');else if(['ps','images'].includes(action))emit(action==='ps'?'CONTAINER ID   IMAGE   COMMAND   STATUS   NAMES':'REPOSITORY   TAG   IMAGE ID   CREATED   SIZE');else{emit('docker: container operations are unavailable; OmniTerm does not start host containers.','warn');code=126;}}
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
    else if (cmd==='test-path') { const envPath=pathArg.toLowerCase().startsWith('env:'),key=pathArg.slice(4).toUpperCase(),exists=envPath?(key?Object.hasOwn(s.env,key):true):!!this.node(session,pathArg);emit(exists?'True':'False');pipe=[exists];code=exists?0:1; }
    else if (cmd==='clear-host') { /* UI handles clear as a regular blank output event */ }
    else if(cmd==='get-command'){const names=['Get-ChildItem','Get-Location','Set-Location','Push-Location','Pop-Location','Get-Item','Test-Path','Get-Content','Set-Content','Add-Content','Clear-Content','New-Item','Remove-Item','Copy-Item','Move-Item','Rename-Item','Get-Service','Start-Service','Stop-Service','Restart-Service','Get-Process','Start-Process','Stop-Process','Get-Date','Start-Sleep','Get-Random','Get-Help','Get-History','Clear-History','Get-Variable','Set-Variable','Remove-Variable','Get-Alias','Set-Alias','Remove-Alias','Get-Command','Write-Output','Write-Host','Write-Warning','Write-Error','Select-String','Select-Object','Where-Object','Sort-Object','Measure-Object','ForEach-Object','Format-Table','Format-List','Out-String','Out-File','Out-Null','Tee-Object','ConvertTo-Json','ConvertFrom-Json','Get-Member','Get-SimulatedNetwork','Set-SimulatedNetwork','Get-NetTCPConnection','Get-NetIPAddress','Get-Volume','Get-Disk','Test-NetConnection','Get-EventLog','git','winget','choco','apt'];names.forEach(value=>emit(value));pipe=names;}
    else if(cmd==='clear-history'){s.history=[];}
    else if(cmd==='start-sleep'){const value=Number(args.find(x=>/^\d+(?:\.\d+)?$/.test(x))||1);s.clock=new Date(Date.parse(s.clock)+Math.max(0,value)*1000).toISOString();}
    else if(cmd==='get-random'){const min=Number(args[0]||0),max=Number(args[1]||2147483647),value=Math.floor(Math.random()*(max-min))+min;emit(value);pipe=[value];}
    else if(cmd==='get-member'){const value=pipeInput?.[0]||{};const properties=value&&typeof value==='object'?Object.keys(value):['Length','ToString'];properties.forEach(key=>emit(`${key}  NoteProperty`));}
    else if(cmd==='set-itemproperty'){emit('Set-ItemProperty: Windows registry access is not modeled in the virtual environment.','warn');code=1;}
    else if(cmd==='set-item'){const key=positional[0]||'',valueAt=args.findIndex(x=>x.toLowerCase()==='-value'),value=valueAt>=0?args[valueAt+1]??'':positional[1]??'';if(key.toLowerCase().startsWith('env:'))s.env[key.slice(4).toUpperCase()]=value;else throw new Error('Set-Item supports only the virtual Env: provider.');}
    else if(cmd==='set-alias'){if(positional.length<2)throw new Error('Set-Alias requires a name and definition.');s.aliases[positional[0].toLowerCase()]=positional[1];}
    else if(cmd==='remove-alias'){positional.forEach(key=>delete s.aliases[key.toLowerCase()]);}
    else if(cmd==='select-string'){const pattern=positional[0]||'',paths=positional.slice(1),lines=pipeInput||paths.flatMap(p=>{const file=this.node(session,p);if(!file||file.dir)throw new Error(`File not found: ${p}`);return file.data.split(/\r?\n/);}),matches=lines.filter(x=>String(x).toLowerCase().includes(pattern.toLowerCase()));matches.forEach(x=>emit(x));pipe=matches;code=matches.length?0:1;}
    else if(cmd==='get-eventlog'){const entry=['Virtual system log','Virtual session started','No host event log is accessed.'];entry.forEach(x=>emit(x));pipe=entry;}
    else if (cmd==='exit') emit('Use the close tab control to finish this virtual session.','dim');
    else { emit(`${name}: command not found in the virtual ${bash?'Bash':'PowerShell'} environment. Try "help" for supported commands.`,'err');code=127; }
    if (redirect) { const prior=this.node(session,redirect);this.put(session,redirect,(append?prior?.data||'':'')+out.map(x=>x.text).join('\n')+'\n');return {output:[],code:0,pipe:[]}; }
    return {output:out,code,pipe};
  }
}

module.exports = { Simulator };
