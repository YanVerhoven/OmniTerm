const $ = selector => document.querySelector(selector);
let sessions = [], activeId = null, settings = {}, pendingSettings = null;
const output = $('#output'), input = $('#command'), terminal = $('#terminal');
document.querySelector('.titlebar').style.webkitAppRegion = 'drag';
document.querySelectorAll('.titlebar button').forEach(button => button.style.webkitAppRegion = 'no-drag');

async function api(op, data = {}) { return window.omni.request(op, data); }
function activeSession() { return sessions.find(item => item.id === activeId); }
function promptFor(session) {
  if (!session) return '';
  if (session.shell === 'bash') {
    let cwd = session.cwd || '/root';
    if (cwd === '/root') cwd = '~'; else if (cwd.startsWith('/root/')) cwd = '~' + cwd.slice(5);
    return `root@vm:${cwd}#`;
  }
  return `PS ${(session.cwd || 'C:/Deploy').replaceAll('/', '\\')}>`;
}
function line(text, style = 'out', command = false) {
  const element = document.createElement('div');
  element.className = `terminal-line ${command ? 'command' : style}`;
  element.textContent = String(text);
  output.append(element);
}
async function renderTranscript(session, animateFrom = null) {
  output.replaceChildren();
  const transcript=session?.transcript||[];
  for (let index=0;index<transcript.length;index++) {
    const item=transcript[index];
    if (item.type === 'command') line(`${item.prompt||promptFor(session)} ${item.text}`, 'command', true);
    else {if(animateFrom!==null&&index>=animateFrom&&settings.speed>0)await new Promise(resolve=>setTimeout(resolve,Math.round(45*settings.speed)));line(item.text, item.style || 'out');}
  }
  $('#welcome').hidden = !!(session?.transcript?.length);
  requestAnimationFrame(() => { output.scrollTop = output.scrollHeight; });
}
async function render(animateFrom = null) {
  const session = activeSession() || sessions[0];
  if (!session) return;
  activeId = session.id;
  const list = $('#sessionList'); list.replaceChildren();
  const recent = [...sessions].sort((a,b) => (b.pinned-a.pinned) || b.createdAt.localeCompare(a.createdAt));
  const group = document.createElement('div'); group.className='session-group-label'; group.textContent='СЕССИИ'; list.append(group);
  for (const item of recent) {
    const row = document.createElement('div'); row.className=`session-item${item.id===activeId?' active':''}`;
    row.title=item.name;
    const glyph=document.createElement('span');glyph.className=`session-icon${item.shell==='bash'?' bash':''}`;glyph.textContent=item.shell==='bash'?'$_':'›_';
    const name=document.createElement('span');name.className='session-name';name.textContent=item.name;
    row.append(glyph,name);
    if(item.pinned){const pin=document.createElement('span');pin.className='session-pin';pin.textContent='◆';row.append(pin);}
    const more=document.createElement('button');more.className='session-more';more.textContent='•••';more.title='Действия';more.onclick=e=>{e.stopPropagation();openSessionMenu(item,e.currentTarget);};row.append(more);
    row.onclick=async()=>{activeId=item.id;await api('activate',{id:item.id});render();input.focus();};list.append(row);
  }
  $('#activeName').textContent=session.name;
  $('#shellLabel').textContent=session.shell==='bash'?'LINUX BASH':'WINDOWS POWERSHELL';
  $('#cwd').textContent=session.cwd|| (session.shell==='bash'?'/root':'C:/Deploy');
  $('#prompt').textContent=promptFor(session);
  $('#sessionExpiry').textContent=session.type==='temporary'?(session.expires?`Удаление через ${remaining(session.expires)}`:'Автоудаление выключено'):'Постоянная сессия';
  $('#pinButton')?.classList.toggle('selected',session.pinned);
  await renderTranscript(session,animateFrom);
  document.documentElement.dataset.theme=settings.theme||'dark';
  document.documentElement.style.setProperty('--terminal-size',`${settings.fontSize||14}px`);
  input.focus();
}
function remaining(expiry) {
  const minutes=Math.max(0,Math.ceil((Date.parse(expiry)-Date.now())/60000));
  if(minutes<60)return `${minutes} мин.`;
  const hours=Math.floor(minutes/60),days=Math.floor(hours/24);
  return days?`${days} дн.`:`${hours} ч.`;
}
async function refresh(options = {}) {
  sessions=await api('list');
  settings=await api('settings');
  if(!sessions.some(item=>item.id===activeId))activeId=sessions.find(item=>item.active)?.id||sessions[0]?.id;
  await render(options.animateFrom??null);
}
function openContext(button, items) {
  const menu=$('#contextMenu');menu.replaceChildren();
  for(const item of items){
    if(item.separator){const hr=document.createElement('hr');menu.append(hr);continue;}
    const option=document.createElement('button');option.textContent=item.label;if(item.danger)option.className='danger';
    option.onclick=async()=>{menu.hidden=true;await item.action();};menu.append(option);
  }
  const rect=button.getBoundingClientRect();menu.style.left=`${Math.min(rect.left,innerWidth-195)}px`;menu.style.top=`${Math.min(rect.bottom+4,innerHeight-menu.offsetHeight-8)}px`;menu.hidden=false;
}
function openSessionMenu(session,button) {
  const items=[
    {label:'Переименовать',action:()=>renameSession(session)},
    {label:session.type==='permanent'?'Сделать временной (12 ч)':'Сделать постоянной',action:async()=>{if(session.type==='permanent')await api('temporary',{id:session.id,hours:settings.ttlHours||12});else await api('permanent',{id:session.id});await refresh();}},
    ...(session.type==='temporary'?[{label:'Продлить на 12 часов',action:async()=>{await api('extend',{id:session.id,hours:12});await refresh();}},{label:session.autoCleanup?'Отключить автоудаление':'Включить автоудаление',action:async()=>{await api('autoCleanup',{id:session.id,enabled:!session.autoCleanup,hours:settings.ttlHours||12});await refresh();}}]:[{label:session.pinned?'Открепить':'Закрепить',action:async()=>{await api('pin',{id:session.id,pinned:!session.pinned});await refresh();}}]),
    {label:'Сбросить виртуальное состояние',danger:true,action:async()=>{if(confirm(`Сбросить виртуальные файлы, процессы и службы в «${session.name}»?`)){await api('reset',{id:session.id});await refresh();}}},
    {label:'Дублировать во временную сессию',action:async()=>{const copy=await api('duplicate',{id:session.id});activeId=copy.id;await refresh();}},
    {separator:true},{label:'Закрыть сессию',action:async()=>{await api('close',{id:session.id});if(activeId===session.id)activeId=null;await refresh();}},
    {label:'Удалить сессию',danger:true,action:async()=>{if(confirm(`Удалить «${session.name}» и её виртуальные данные?`)){await api('delete',{id:session.id});if(activeId===session.id)activeId=null;await refresh();}}}
  ];openContext(button,items);
}
async function createSession(shell,type='temporary') {
  const result=await api('create',{shell:shell||settings.defaultShell||'ps',type,ttlHours:settings.ttlHours||12});
  activeId=result.id;await refresh();
}
async function renameSession(session=activeSession()) {
  if(!session)return;const name=prompt('Название сессии',session.name);
  if(name?.trim()){await api('rename',{id:session.id,name});await refresh();}
}
async function execute() {
  const source=input.value.trim();if(!source)return;
  const session=activeSession();if(!session)return;
  const transcriptLength=session.transcript?.length||0;
  input.value='';resizeInput();$('#runCommand').disabled=true;$('#welcome').hidden=true;
  try { const result=await api('command',{id:session.id,command:source});if(result.activeId)activeId=result.activeId;await refresh({animateFrom:result.activeId===session.id?transcriptLength:null}); }
  catch(error){line(error.message,'err');}
  $('#runCommand').disabled=false;input.focus();output.scrollTop=output.scrollHeight;
}
function resizeInput(){input.style.height='auto';input.style.height=`${Math.min(input.scrollHeight,160)}px`;}
input.addEventListener('input',resizeInput);
input.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();execute();}
});
$('#runCommand').onclick=execute;
$('#newSession').onclick=event=>openContext(event.currentTarget,[
  {label:`＋  Новая сессия · ${settings.defaultShell==='bash'?'Linux Bash':'Windows PowerShell'}`,action:()=>createSession(settings.defaultShell||'ps')},
  {label:settings.defaultShell==='bash'?'＋  Windows PowerShell':'＋  Linux Bash',action:()=>createSession(settings.defaultShell==='bash'?'ps':'bash')},
  {separator:true},{label:'＋  Новая постоянная сессия',action:()=>createSession(settings.defaultShell||'ps','permanent')}
]);
$('#rename').onclick=()=>renameSession();
$('#sessionMenu').onclick=e=>{const s=activeSession();if(s)openSessionMenu(s,e.currentTarget);};
$('#minimize').onclick=()=>window.omni.window('minimize');
$('#maximize').onclick=()=>window.omni.window('maximize');
$('#close').onclick=()=>window.omni.window('close');
$('#screenshot').onclick=async()=>{try{const file=await window.omni.screenshot();const label=$('.status-right');const saved=label.textContent;label.textContent=`Скриншот сохранён: ${file}`;setTimeout(()=>label.textContent=saved,4000);}catch(error){line(error.message,'err');}};
$('#brandHome').onclick=()=>input.focus();
$('#searchSessions').onclick=()=>{
  const query=prompt('Найти сессию');if(query===null)return;
  document.querySelectorAll('.session-item').forEach(row=>row.hidden=!row.querySelector('.session-name').textContent.toLowerCase().includes(query.toLowerCase()));
};
document.querySelectorAll('.suggestions button').forEach(button=>button.onclick=()=>{input.value=button.dataset.command;execute();});

function settingRow(title,subtitle,control) {
  const row=document.createElement('div');row.className='setting-row';
  const copy=document.createElement('div');copy.className='setting-copy';
  const bold=document.createElement('b');bold.textContent=title;
  const small=document.createElement('small');small.textContent=subtitle;copy.append(bold,small);
  const wrap=document.createElement('div');wrap.className='setting-control';wrap.append(control);row.append(copy,wrap);return row;
}
function selectControl(id,options,value) {
  const select=document.createElement('select');select.id=id;
  for(const [label,key] of options){const option=document.createElement('option');option.value=key;option.textContent=label;select.append(option);}
  select.value=value;return select;
}
function openSettings() {
  pendingSettings={...settings};$('#modalTitle').textContent='Настройки';$('#modalContent').replaceChildren();
  $('#saveModal').hidden=false;
  const content=$('#modalContent');
  content.append(
    settingRow('Тема','Цвет рабочего пространства',selectControl('settingTheme',[['Тёмная','dark'],['Светлая','light']],pendingSettings.theme||'dark')),
    settingRow('Новая сессия','Оболочка по умолчанию',selectControl('settingShell',[['Windows PowerShell','ps'],['Linux Bash','bash']],pendingSettings.defaultShell||'ps'))
  );
  const size=document.createElement('input');size.type='range';size.min=11;size.max=20;size.step=1;size.value=pendingSettings.fontSize||14;
  const sizeLabel=document.createElement('span');sizeLabel.textContent=`${size.value} px`;sizeLabel.style.cssText='display:inline-block;width:34px;color:#aaa;font:10px var(--mono);text-align:right';
  size.oninput=()=>sizeLabel.textContent=`${size.value} px`;
  const sizeControl=document.createElement('span');sizeControl.append(size,sizeLabel);
  content.append(settingRow('Размер текста','Шрифт виртуальной консоли',sizeControl));
  const speed=selectControl('settingSpeed',[['Мгновенно','0'],['Быстро','0.5'],['Обычно','1'],['Медленно','2']],String(pendingSettings.speed??1));
  content.append(settingRow('Скорость вывода','Паузы в симулированных командах',speed));
  const ttl=selectControl('settingTTL',[['1 час','1'],['6 часов','6'],['12 часов','12'],['24 часа','24'],['7 дней','168']],String(pendingSettings.ttlHours||12));
  content.append(settingRow('Срок временной сессии','После этого виртуальная сессия удаляется',ttl));
  $('#modalBackdrop').hidden=false;$('#closeModal').focus();
}
function closeModal(){$('#modalBackdrop').hidden=true;}
$('#settingsButton').onclick=openSettings;
$('#closeModal').onclick=closeModal;$('#cancelModal').onclick=closeModal;
$('#modalBackdrop').onclick=e=>{if(e.target.id==='modalBackdrop')closeModal();};
$('#saveModal').onclick=async()=>{
  settings={...settings,theme:$('#settingTheme').value,defaultShell:$('#settingShell').value,fontSize:Number(document.querySelector('.setting-control input[type=range]').value),speed:Number($('#settingSpeed').value),ttlHours:Number($('#settingTTL').value)};
  await api('saveSettings',{settings});closeModal();render();
};
$('#historyButton').onclick=async()=>{
  const entries=await api('history');$('#modalTitle').textContent='История очистки';$('#modalContent').replaceChildren();
  $('#saveModal').hidden=true;
  if(!entries.length){const empty=document.createElement('p');empty.textContent='Записей пока нет.';empty.style.color='#999';$('#modalContent').append(empty);}
  for(const entry of entries){const row=document.createElement('div');row.className='setting-row';const copy=document.createElement('div');copy.className='setting-copy';const b=document.createElement('b');b.textContent=entry.name||entry.sessionId;const small=document.createElement('small');small.textContent=`${entry.event} · ${new Date(entry.at).toLocaleString()}`;copy.append(b,small);row.append(copy);$('#modalContent').append(row);}
  $('#modalTitle').textContent='История очистки';$('#modalBackdrop').hidden=false;$('#saveModal').hidden=true;
};
$('#restoreButton').onclick=async event=>{
  const closed=await api('listClosed');
  if(!closed.length){openContext(event.currentTarget,[{label:'Нет закрытых сессий',action:async()=>{}}]);return;}
  openContext(event.currentTarget,closed.map(session=>({label:`↶  ${session.name} · ${session.shell==='bash'?'Bash':'PowerShell'}`,action:async()=>{await api('restore',{id:session.id});activeId=session.id;await refresh();}})));
};
document.addEventListener('click',e=>{if(!e.target.closest('#contextMenu')&&!e.target.closest('.session-more')&&e.target.id!=='newSession')$('#contextMenu').hidden=true;});
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#newSession').click();}
  if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='n'){e.preventDefault();createSession(settings.defaultShell||'ps','permanent');}
  else if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='t'){e.preventDefault();$('#restoreButton').click();}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='t'&&!e.shiftKey){e.preventDefault();createSession(settings.defaultShell||'ps');}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='w'){e.preventDefault();const s=activeSession();if(s)api('close',{id:s.id}).then(()=>{activeId=null;return refresh();});}
  else if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='p'){e.preventDefault();const command=prompt('Команда виртуальной сессии');if(command){input.value=command;execute();}}
  else if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='s'){e.preventDefault();api('save');}
  else if((e.ctrlKey||e.metaKey)&&e.key==='Tab'){e.preventDefault();const at=sessions.findIndex(item=>item.id===activeId),delta=e.shiftKey?-1:1,next=sessions[(at+delta+sessions.length)%sessions.length];if(next){activeId=next.id;api('activate',{id:activeId}).then(refresh);}}
  else if((e.ctrlKey||e.metaKey)&&/^[1-9]$/.test(e.key)){e.preventDefault();const target=sessions[Number(e.key)-1];if(target){activeId=target.id;api('activate',{id:activeId}).then(refresh);}}
  else if((e.ctrlKey||e.metaKey)&&(e.key==='='||e.key==='+')){e.preventDefault();settings.fontSize=Math.min(24,(settings.fontSize||14)+1);api('saveSettings',{settings}).then(render);}
  else if((e.ctrlKey||e.metaKey)&&e.key==='-'){e.preventDefault();settings.fontSize=Math.max(10,(settings.fontSize||14)-1);api('saveSettings',{settings}).then(render);}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='l'){e.preventDefault();const s=activeSession();if(s)api('command',{id:s.id,command:'clear'}).then(refresh);}
  if(e.key==='F12'){e.preventDefault();$('#screenshot').click();}
  if(e.key==='Escape'){closeModal();$('#contextMenu').hidden=true;}
});
refresh().then(()=>{if(!activeSession())$('#welcome').hidden=false;}).catch(error=>line(error.message,'err'));
