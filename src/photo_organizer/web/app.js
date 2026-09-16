const $=s=>document.querySelector(s);let csrf='',lastSnapshot=null,previewOffset=0;
const newMediaEpoch=()=>`${Date.now()}-${Math.random().toString(36).slice(2)}`;
let mediaEpoch=newMediaEpoch(),sessionEpoch=0,displayUser=null,photoRequest=0;
function clearPrivateView(){
  sessionEpoch++;photoRequest++;mediaEpoch=newMediaEpoch();displayUser=null;csrf='';
  galleryItems=[];mapItems=[];currentPhotoId=null;lastSnapshot=null;selectedPhotos.clear();
  for(const id of ['rows','galleryGrid','galleryFolders','albumGrid','trashList','historyRows','exifDetails','backupList','userList'])$('#'+id).replaceChildren();
  $('#lightbox').hidden=true;$('#lightboxImage').removeAttribute('src');$('#lightboxName').textContent='';$('#lightboxMeta').textContent='';
  liveVideo.pause();liveVideo.removeAttribute('src');liveVideo.load();mapPhotoLayer?.clearLayers();photoMap?.closePopup();albumItems=[];albumId=null;mapBounds=null;
  $('#selectionBar').hidden=true;$('#login').classList.remove('hidden');$('#password').value='';
}
const nativeFetch=window.fetch.bind(window);
window.addEventListener('unhandledrejection',event=>{if(event.reason?.message==='登入狀態已變更')event.preventDefault()});
window.fetch=async function(...args){
  const epoch=sessionEpoch,response=await nativeFetch(...args);
  if(epoch!==sessionEpoch)throw new Error('登入狀態已變更');
  const json=response.json.bind(response);
  response.json=async()=>{const value=await json();if(epoch!==sessionEpoch)throw new Error('登入狀態已變更');return value};
  return response;
};
const toast=m=>{const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600)};
async function action(name){try{const r=await fetch('/api/'+name,{method:'POST',headers:{'X-CSRF-Token':csrf}}),x=await r.json();if(!r.ok)throw Error(x.error);toast('工作已開始')}catch(e){toast(e.message)}}
$('#scan').onclick=()=>action('scan');$('#import').onclick=()=>{const s=lastSnapshot,gb=((s?.total_bytes||0)/1073741824).toFixed(2),message=`整理前確認\n\n總計：${s?.total||0} 個檔案（${gb} GB）\n新照片：${s?.counts?.IMPORT||0}\n重複照片：${s?.counts?.DUPLICATE||0}\n沒有日期：${s?.counts?.UNSORTED||0}\n\n目的檔驗證成功後才會移除 Inbox 來源。`;if(confirm(message))action('import')};$('#backup').onclick=()=>action('backup');$('#reindex').onclick=()=>confirm('重新掃描你的正式圖庫？照片不會被移動。')&&action('reindex');
$('#loginForm').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('#username').value,password:$('#password').value})}),x=await r.json();if(!r.ok){$('#loginError').textContent=x.error;return}clearPrivateView();csrf=x.csrf;displayUser=x.username;$('#login').classList.add('hidden')};
$('#logout').onclick=async()=>{try{await fetch('/api/logout',{method:'POST'})}finally{clearPrivateView()}};
$('#users').onclick=async()=>{const username=prompt('新使用者帳號（英文字母、數字、- 或 _）');if(!username)return;const password=prompt('新使用者密碼（不可空白）');if(!password)return;const r=await fetch('/api/users',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({username,password,role:'user'})}),x=await r.json();toast(r.ok?'使用者已建立':x.error)};
function render(s){if(displayUser!==null&&displayUser!==s.user.username)clearPrivateView();displayUser=s.user.username;csrf=s.user.csrf;$('#login').classList.add('hidden');$('#appVersion').textContent='v'+(s.system.version||'未知');$('#users').hidden=s.user.role!=='admin';$('#dot').className='on';$('#health').textContent=s.user.username+' · '+(s.busy?'運作中':'服務正常');$('#exif').textContent=s.system.exiftool?'ExifTool '+s.system.exiftool:'ExifTool 無法使用';$('#message').textContent=s.error||s.message;$('#bar').style.width=s.progress+'%';$('#total').textContent=s.total;$('#new').textContent=s.counts.IMPORT||0;$('#duplicate').textContent=s.counts.DUPLICATE||0;$('#unsorted').textContent=s.counts.UNSORTED||0;$('#inboxes').textContent=s.system.inboxes.join(' · ');$('#library').textContent=s.system.library;$('#backupPath').textContent=s.system.backup;$('#scan').disabled=s.busy;$('#import').disabled=s.busy||!s.total;$('#backup').disabled=s.busy;$('#reindex').disabled=s.busy;const rows=$('#rows');if(!s.items.length)rows.innerHTML='<tr><td colspan="5" class="empty">按「掃描 Inbox」查看照片</td></tr>';else rows.innerHTML=s.items.map((x,i)=>`<tr><td><span class="badge">${x.action}</span></td><td><img class="inbox-thumb" src="/api/inbox-thumbnail/${i}?session=${mediaEpoch}" alt=""></td><td>${esc(x.source.split(/[\\/]/).pop())}</td><td>${x.capture_date?esc(x.capture_date.slice(0,19).replace('T',' ')):'未知'}</td><td title="${esc(x.destination)}">${esc(x.destination)}</td></tr>`).join('')}
function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function poll(){try{const r=await fetch(`/api/status?offset=${previewOffset}`);if(r.status===401){if(displayUser!==null)clearPrivateView();$('#login').classList.remove('hidden');return setTimeout(poll,1500)}lastSnapshot=await r.json();render(lastSnapshot)}catch(e){$('#health').textContent='無法連線';$('#dot').className=''}setTimeout(poll,1000)}poll();
let galleryOffset=0,galleryTotal=0,galleryYear='',galleryDay='',galleryItems=[],currentPhotoId=null;
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>{document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));button.classList.add('active');const view=button.dataset.view;document.querySelectorAll('.view').forEach(x=>x.classList.add('hidden-view'));$('#'+view).classList.remove('hidden-view');$('#pageTitle').textContent={overview:'總覽',gallery:'照片圖庫',history:'操作紀錄'}[view];if(view==='gallery')loadGallery(true);if(view==='history')loadHistory()});
async function loadFolders(){
  const r=await fetch('/api/library-groups');if(!r.ok)return;
  const data=await r.json(),years=new Map();
  // Numeric object keys enumerate ascending; explicitly sort the date tree instead.
  for(const group of data.items){
    if(!years.has(group.year))years.set(group.year,[]);
    years.get(group.year).push(group);
  }
  const newestFirst=(a,b)=>(a==='日期未知')-(b==='日期未知')||b.localeCompare(a);
  let html='<button class="folder all active" data-year="" data-day="">▦ 所有照片</button>';
  [...years.entries()].sort(([a],[b])=>newestFirst(a,b)).forEach(([year,days])=>{
    html+=`<details><summary>${esc(year)} <small>${days.reduce((n,x)=>n+x.count,0)}</small></summary>`;
    days.sort((a,b)=>newestFirst(a.day,b.day)).forEach(x=>html+=`<button class="folder" data-year="${esc(year)}" data-day="${esc(x.day)}">${esc(x.day==='日期未知'?x.day:x.day.slice(5))}<small>${x.count}</small></button>`);
    html+='</details>';
  });
  $('#galleryFolders').innerHTML=html;
  $('#galleryFolders').querySelectorAll('.folder').forEach(b=>b.onclick=()=>{
    $('#galleryFolders').querySelectorAll('.folder').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');galleryYear=b.dataset.year;galleryDay=b.dataset.day;loadGallery(true);
  });
}
async function loadGallery(reset=false){if(reset){galleryOffset=0;galleryItems=[];$('#galleryGrid').innerHTML='';if(!$('#galleryFolders').children.length)await loadFolders()}const type=$('#galleryType').value,filter=`&year=${encodeURIComponent(galleryYear)}&day=${encodeURIComponent(galleryDay)}`,r=await fetch(`/api/library?offset=${galleryOffset}&limit=60&type=${type}${filter}`);if(!r.ok)return;const data=await r.json();galleryTotal=data.total;galleryItems.push(...data.items);$('#galleryCount').textContent=`${galleryDay||galleryYear||'所有照片'} · 共 ${data.total} 個檔案`;if(reset&&!data.items.length)$('#galleryGrid').innerHTML='<div class="gallery-empty">這個分類目前沒有照片</div>';data.items.forEach(item=>{const card=document.createElement('button');card.className='photo-card';const name=item.current_path.split(/[\\/]/).pop();card.innerHTML=`<img loading="lazy" src="/api/thumbnail/${item.id}?session=${mediaEpoch}" alt="${esc(name)}"><div><b>${esc(name)}</b><small>${item.file_type} · ${item.capture_date?esc(item.capture_date.slice(0,10)):'日期未知'}</small></div>`;card.onclick=()=>openPhoto(item,name);$('#galleryGrid').appendChild(card)});galleryOffset+=data.items.length;$('#galleryMore').hidden=galleryOffset>=galleryTotal}
let previewZoom=1;function setPreviewZoom(value){previewZoom=Math.min(4,Math.max(1,value));$('#lightboxImage').style.transform=`scale(${previewZoom})`;$('#lightboxImage').style.cursor=previewZoom>1?'zoom-out':'zoom-in'}
async function openPhoto(item,name){const request=++photoRequest;currentPhotoId=item.id;setPreviewZoom(1);$('#lightboxImage').src=`/api/preview/${item.id}?session=${mediaEpoch}`;$('#lightboxName').textContent=name;$('#lightboxMeta').textContent=`${item.file_type} · ${item.capture_date||'日期未知'}`;$('#exifDetails').innerHTML='<p>正在讀取…</p>';$('#lightbox').hidden=false;getSelection()?.removeAllRanges();const r=await fetch(`/api/photo-info/${item.id}`);if(!r.ok)return $('#exifDetails').innerHTML='<p>無法讀取照片資訊</p>';const x=await r.json();if(request!==photoRequest||$('#lightbox').hidden)return;const e=x.exif,labels={DateTimeOriginal:'拍攝時間',Make:'品牌',Model:'相機機身',LensModel:'鏡頭',LensID:'鏡頭',FocalLength:'焦距',FNumber:'光圈',ExposureTime:'快門',ShutterSpeed:'快門速度',ISO:'ISO',ExposureCompensation:'曝光補償',Flash:'閃光燈',ImageWidth:'寬度',ImageHeight:'高度',GPSPosition:'GPS 位置',GPSAltitude:'海拔'};const seen=new Set(),rows=Object.entries(e).filter(([k])=>{const label=labels[k];if(!label||seen.has(label))return false;seen.add(label);return true}).map(([k,v])=>`<div><span>${labels[k]}</span><b>${esc(v)}</b></div>`);$('#exifDetails').innerHTML=rows.length?rows.join(''):'<p>這張照片沒有可顯示的 EXIF 資訊</p>'}function navigatePhoto(step){const i=galleryItems.findIndex(x=>x.id===currentPhotoId),item=galleryItems[i+step];if(item)openPhoto(item,item.current_path.split(/[\\/]/).pop())}$('#lightboxPhoto').onwheel=e=>{e.preventDefault();setPreviewZoom(previewZoom+(e.deltaY<0?.2:-.2))};$('#lightboxPhoto').ondblclick=()=>setPreviewZoom(1);$('#lightboxPrev').onclick=e=>{e.stopPropagation();navigatePhoto(-1)};$('#lightboxNext').onclick=e=>{e.stopPropagation();navigatePhoto(1)};document.addEventListener('keydown',e=>{if($('#lightbox').hidden)return;if(e.key==='ArrowLeft')navigatePhoto(-1);if(e.key==='ArrowRight')navigatePhoto(1);if(e.key==='Escape')$('#lightbox').hidden=true});$('#lightboxClose').onclick=()=>$('#lightbox').hidden=true;$('#lightbox').onclick=e=>{if(e.target===$('#lightbox'))$('#lightbox').hidden=true};$('#galleryMore').onclick=()=>loadGallery();$('#galleryType').onchange=()=>loadGallery(true);
async function loadHistory(){const r=await fetch('/api/history');if(!r.ok)return;const data=await r.json();$('#historyRows').innerHTML=data.items.length?data.items.map(x=>`<tr><td>${esc(x.created_at.slice(0,19).replace('T',' '))}</td><td>${esc(x.action)}</td><td><span class="badge">${esc(x.status)}</span></td><td title="${esc(x.source_path)}">${esc(x.source_path)}</td><td title="${esc(x.error||x.destination_path||'')}">${esc(x.error||x.destination_path||'—')}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">尚無操作紀錄</td></tr>'}

const selectedPhotos=new Set();let favoritesOnly=false,searchTimer;
function updateSelection(){const n=selectedPhotos.size;$('#selectionBar').hidden=!n;$('#selectionCount').textContent=`已選擇 ${n} 張`;document.querySelectorAll('.photo-card').forEach(card=>card.classList.toggle('selected',selectedPhotos.has(Number(card.dataset.id))))}
async function toggleFavorite(item,button){item.favorite=item.favorite?0:1;button.classList.toggle('on',!!item.favorite);button.textContent=item.favorite?'★':'☆';const r=await fetch('/api/favorite',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({id:item.id,favorite:!!item.favorite})});if(!r.ok){item.favorite=item.favorite?0:1;toast('最愛狀態儲存失敗')}else if(favoritesOnly&&!item.favorite)loadGallery(true)}
async function loadGallery(reset=false){if(reset){galleryOffset=0;galleryItems=[];selectedPhotos.clear();updateSelection();$('#galleryGrid').innerHTML='';if(!$('#galleryFolders').children.length)await loadFolders()}const type=$('#galleryType').value,q=$('#gallerySearch').value.trim(),filter=`&year=${encodeURIComponent(galleryYear)}&day=${encodeURIComponent(galleryDay)}&q=${encodeURIComponent(q)}&favorites=${favoritesOnly?1:0}`,r=await fetch(`/api/library?offset=${galleryOffset}&limit=60&type=${type}${filter}`);if(!r.ok)return;const data=await r.json();galleryTotal=data.total;galleryItems.push(...data.items);$('#galleryCount').textContent=`${galleryDay||galleryYear||'所有照片'} · 共 ${data.total} 個檔案`;if(reset&&!data.items.length)$('#galleryGrid').innerHTML='<div class="gallery-empty">找不到符合條件的照片</div>';data.items.forEach(item=>{const card=document.createElement('article');card.className='photo-card';card.dataset.id=item.id;card.tabIndex=0;const name=item.current_path.split(/[\\/]/).pop();card.innerHTML=`<button class="card-select" aria-label="選取照片">${selectedPhotos.has(item.id)?'✓':'○'}</button><button class="favorite-star ${item.favorite?'on':''}" aria-label="最愛">${item.favorite?'★':'☆'}</button><img loading="lazy" src="/api/thumbnail/${item.id}?session=${mediaEpoch}" alt="${esc(name)}"><div><b>${esc(name)}</b><small>${item.file_type} · ${item.capture_date?esc(item.capture_date.slice(0,10)):'日期未知'}</small></div>`;card.querySelector('.card-select').onclick=e=>{e.stopPropagation();selectedPhotos.has(item.id)?selectedPhotos.delete(item.id):selectedPhotos.add(item.id);e.currentTarget.textContent=selectedPhotos.has(item.id)?'✓':'○';updateSelection()};card.querySelector('.favorite-star').onclick=e=>{e.stopPropagation();toggleFavorite(item,e.currentTarget)};card.onclick=()=>openPhoto(item,name);card.onkeydown=e=>{if(e.key==='Enter')openPhoto(item,name)};$('#galleryGrid').appendChild(card)});galleryOffset+=data.items.length;$('#galleryMore').hidden=galleryOffset>=galleryTotal}
$('#gallerySearch').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadGallery(true),300)};$('#favoriteFilter').onclick=()=>{favoritesOnly=!favoritesOnly;$('#favoriteFilter').classList.toggle('active',favoritesOnly);$('#favoriteFilter').textContent=favoritesOnly?'★ 最愛':'☆ 最愛';loadGallery(true)};$('#selectAll').onclick=()=>{galleryItems.forEach(x=>selectedPhotos.add(x.id));updateSelection()};$('#clearSelection').onclick=()=>{selectedPhotos.clear();updateSelection()};$('#downloadSelected').onclick=async()=>{const button=$('#downloadSelected');button.disabled=true;button.textContent='正在準備…';try{const r=await fetch('/api/download',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({ids:[...selectedPhotos]})});if(!r.ok)throw Error((await r.json()).error);const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='photo-organizer-selection.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000)}catch(e){toast(e.message)}finally{button.disabled=false;button.textContent='下載 ZIP'}};

async function postJson(url,body={}){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)}),x=await r.json();if(!r.ok)throw Error(x.error||'操作失敗');return x}
const albumButton=document.createElement('button');albumButton.id='addSelectedAlbum';albumButton.textContent='加入相簿';$('#selectionBar').insertBefore(albumButton,$('#downloadSelected'));const trashButton=document.createElement('button');trashButton.id='trashSelected';trashButton.textContent='移到回收站';$('#selectionBar').insertBefore(trashButton,$('#downloadSelected'));
async function loadAlbums(){const r=await fetch('/api/albums'),x=await r.json();$('#albumGrid').innerHTML=x.items.length?x.items.map(a=>`<article><h3>${esc(a.name)}</h3><p>${esc(a.description||'私人相簿')} · ${a.count} 張</p><button data-album="${a.id}">查看相簿</button></article>`).join(''):'<article><h3>尚無相簿</h3><p>建立相簿後，可從照片圖庫批次加入照片。</p></article>'}
$('#createAlbum').onclick=async()=>{const name=prompt('相簿名稱');if(!name)return;try{await postJson('/api/albums',{name});loadAlbums()}catch(e){toast(e.message)}};albumButton.onclick=async()=>{const r=await fetch('/api/albums'),x=await r.json();if(!x.items.length)return toast('請先建立相簿');const names=x.items.map(a=>`${a.id}: ${a.name}`).join('\n'),id=Number(prompt(`輸入相簿編號：\n${names}`));if(!id)return;try{const y=await postJson(`/api/album/${id}/photos`,{ids:[...selectedPhotos]});toast(`已加入 ${y.added} 張照片`)}catch(e){toast(e.message)}};
trashButton.onclick=async()=>{if(!selectedPhotos.size||!confirm(`確定將 ${selectedPhotos.size} 張照片移到回收站？`))return;try{const x=await postJson('/api/trash',{ids:[...selectedPhotos]});toast(`已移入回收站 ${x.completed} 張`);loadGallery(true)}catch(e){toast(e.message)}};
async function loadTrash(){const r=await fetch('/api/trash'),x=await r.json();$('#trashList').innerHTML=x.items.length?x.items.map(i=>`<label class="trash-row"><input type="checkbox" value="${i.id}"><b>${esc(i.current_path.split(/[\\/]/).pop())}</b><small>${esc((i.deleted_at||'').slice(0,19).replace('T',' '))}</small></label>`).join(''):'<div class="empty">回收站是空的</div>'}
$('#restoreTrash').onclick=async()=>{const ids=[...document.querySelectorAll('#trashList input:checked')].map(x=>Number(x.value));if(!ids.length)return toast('請先選擇照片');try{const x=await postJson('/api/trash-restore',{ids});toast(`已還原 ${x.completed} 張`);loadTrash()}catch(e){toast(e.message)}};
async function loadMap(){const r=await fetch('/api/map'),x=await r.json();$('#mapCount').textContent=`共 ${x.items.length} 張具有位置資訊的照片`;$('#mapGrid').innerHTML=x.items.length?x.items.map(i=>{const n=i.current_path.split(/[\\/]/).pop();return `<article class="photo-card" data-map-id="${i.id}"><img loading="lazy" src="/api/thumbnail/${i.id}?session=${mediaEpoch}" alt="${esc(n)}"><div><b>${esc(n)}</b><small>${i.gps_latitude.toFixed(5)}, ${i.gps_longitude.toFixed(5)}</small><a target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${i.gps_latitude}&mlon=${i.gps_longitude}#map=15/${i.gps_latitude}/${i.gps_longitude}">在地圖開啟</a></div></article>`}).join(''):'<div class="gallery-empty">目前沒有具有 GPS 的照片；重新索引後會讀取既有照片的位置。</div>'}
async function loadSettings(){const [s,b,u]=await Promise.all([fetch('/api/settings').then(r=>r.json()),fetch('/api/backups').then(r=>r.json()),fetch('/api/users').then(async r=>r.ok?r.json():null)]);const v=s.settings||{};$('#autoScan').checked=String(v.auto_scan).toLowerCase()==='true';$('#autoImport').checked=String(v.auto_import).toLowerCase()==='true';$('#scheduleMinutes').value=v.schedule_minutes||60;$('#notificationUrl').value=v.notification_url||'';$('#backupList').innerHTML=b.items.slice(0,10).map(x=>`<div class="mini-row"><b>${esc(x.name)}</b><small>${Math.round(x.size/1024)} KB</small><button data-restore="${esc(x.name)}">還原</button></div>`).join('')||'<p>尚無備份</p>';document.querySelectorAll('[data-restore]').forEach(button=>button.onclick=async()=>{if(!confirm(`確定還原 ${button.dataset.restore}？系統會先備份目前索引。`))return;try{await postJson('/api/backup-restore',{name:button.dataset.restore});toast('索引已還原，請重新整理頁面')}catch(e){toast(e.message)}});if(!u){$('#userAdmin').hidden=true}else{$('#userAdmin').hidden=false;$('#userList').innerHTML=u.users.map(x=>`<div class="mini-row"><b>${esc(x.username)}</b><small>${(x.library_bytes/1073741824).toFixed(2)} GB · ${x.active?'啟用':'停用'}</small><button data-active="${x.id}" data-value="${x.active?0:1}">${x.active?'停用':'啟用'}</button><button data-password="${x.id}">重設密碼</button></div>`).join('');document.querySelectorAll('[data-active]').forEach(b=>b.onclick=async()=>{await postJson('/api/user-active',{id:Number(b.dataset.active),active:b.dataset.value==='1'});loadSettings()});document.querySelectorAll('[data-password]').forEach(b=>b.onclick=async()=>{const password=prompt('輸入新密碼（不可空白）');if(password)await postJson('/api/user-password',{id:Number(b.dataset.password),password})})}}
$('#saveSettings').onclick=async()=>{try{await postJson('/api/settings',{auto_scan:$('#autoScan').checked,auto_import:$('#autoImport').checked,schedule_minutes:$('#scheduleMinutes').value,notification_url:$('#notificationUrl').value});toast('設定已儲存')}catch(e){toast(e.message)}};$('#runHealth').onclick=async()=>{$('#healthSummary').textContent='正在檢查…';const r=await fetch('/api/health-check'),x=await r.json();$('#healthSummary').textContent=`資料庫：${x.database}；遺失 ${x.missing_count}；未索引 ${x.unindexed_count}`};$('#settingsBackup').onclick=()=>action('backup');
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>{document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));button.classList.add('active');const view=button.dataset.view;document.querySelectorAll('.view').forEach(x=>x.classList.add('hidden-view'));$('#'+view).classList.remove('hidden-view');$('#pageTitle').textContent={overview:'總覽',gallery:'照片圖庫',albums:'私人相簿',map:'拍攝地圖',trash:'回收站',history:'操作紀錄',settings:'管理中心'}[view];if(view==='gallery')loadGallery(true);if(view==='albums')loadAlbums();if(view==='map')loadMap();if(view==='trash')loadTrash();if(view==='history')loadHistory();if(view==='settings')loadSettings()});
$('#albumGrid').onclick=async e=>{const button=e.target.closest('[data-album]');if(!button)return;const r=await fetch(`/api/album/${button.dataset.album}`),x=await r.json();$('#albumGrid').innerHTML=x.items.length?x.items.map(i=>{const n=i.current_path.split(/[\\/]/).pop();return `<article class="photo-card album-photo" data-photo="${i.id}"><img loading="lazy" src="/api/thumbnail/${i.id}?session=${mediaEpoch}" alt="${esc(n)}"><div><b>${esc(n)}</b><small>${i.file_type} · ${(i.capture_date||'日期未知').slice(0,10)}</small></div></article>`}).join(''):'<article><h3>相簿目前沒有照片</h3></article>';$('#albumGrid').querySelectorAll('[data-photo]').forEach(card=>{const item=x.items.find(i=>i.id===Number(card.dataset.photo));card.onclick=()=>openPhoto(item,item.current_path.split(/[\\/]/).pop())})};
const purgeButton=document.createElement('button');purgeButton.id='purgeTrash';purgeButton.textContent='清除過期項目';$('#restoreTrash').after(purgeButton);const trashLabel=document.createElement('label');trashLabel.innerHTML='回收站保留天數<input type="number" id="trashDays" min="1" value="30">';$('#saveSettings').before(trashLabel);purgeButton.onclick=async()=>{const days=Number($('#trashDays')?.value||30);if(!confirm(`將永久刪除回收站中超過 ${days} 天的照片，確定繼續？`))return;try{const x=await postJson('/api/trash-purge',{days,confirm:true});toast(`已永久清除 ${x.purged} 張`);loadTrash()}catch(e){toast(e.message)}};$('#saveSettings').onclick=async()=>{try{await postJson('/api/settings',{auto_scan:$('#autoScan').checked,auto_import:$('#autoImport').checked,schedule_minutes:$('#scheduleMinutes').value,trash_days:$('#trashDays').value,auto_purge:$('#autoPurge').checked,notification_url:$('#notificationUrl').value});toast('設定已儲存')}catch(e){toast(e.message)}};
const repairButton=document.createElement('button');repairButton.textContent='一鍵安全修復';$('#runHealth').after(repairButton);repairButton.onclick=()=>confirm('修復會標記遺失項目並重新建立索引，但不會移動照片。確定繼續？')&&action('repair');const originalLoadSettings=loadSettings;loadSettings=async()=>{await originalLoadSettings();const r=await fetch('/api/settings'),x=await r.json();$('#trashDays').value=x.settings?.trash_days||30};
const liveVideo=document.createElement('video');liveVideo.id='liveVideo';liveVideo.controls=true;liveVideo.loop=true;liveVideo.hidden=true;$('#lightboxPhoto').insertBefore(liveVideo,$('.zoom-hint'));const livePlay=document.createElement('button');livePlay.className='live-play';livePlay.textContent='▶ 播放 Live Photo';livePlay.hidden=true;$('.exif-panel').appendChild(livePlay);let currentIsLive=false;const originalOpenPhoto=openPhoto;openPhoto=async(item,name)=>{liveVideo.pause();liveVideo.hidden=true;$('#lightboxImage').hidden=false;livePlay.textContent='▶ 播放 Live Photo';currentIsLive=!!item.live_partner_id;livePlay.hidden=!currentIsLive;await originalOpenPhoto(item,name);if(currentPhotoId!==item.id||$('#lightbox').hidden)return;if(!currentIsLive){const r=await fetch(`/api/photo-info/${item.id}`);if(r.ok){const x=await r.json();if(currentPhotoId!==item.id||$('#lightbox').hidden)return;currentIsLive=!!x.live;livePlay.hidden=!currentIsLive}}};livePlay.onclick=()=>{if(liveVideo.hidden){liveVideo.src=`/api/live-video/${currentPhotoId}?session=${mediaEpoch}`;liveVideo.hidden=false;$('#lightboxImage').hidden=true;liveVideo.play();livePlay.textContent='▣ 顯示靜態照片'}else{liveVideo.pause();liveVideo.hidden=true;$('#lightboxImage').hidden=false;livePlay.textContent='▶ 播放 Live Photo'}};$('#lightboxClose').addEventListener('click',()=>liveVideo.pause());const preLiveLoadGallery=loadGallery;loadGallery=async(reset=false)=>{await preLiveLoadGallery(reset);document.querySelectorAll('.photo-card[data-id]').forEach(card=>{const item=galleryItems.find(x=>x.id===Number(card.dataset.id));if(item?.live_partner_id&&!card.querySelector('.live-badge'))card.insertAdjacentHTML('afterbegin','<span class="live-badge">LIVE</span>')})};const liveTools=document.createElement('article');liveTools.innerHTML='<h3>既有 Live Photos</h3><p id="liveMigrationSummary">先重新索引，系統會配對同檔名的 JPG/HEIC 與 MOV。</p><button id="previewLiveMigration">檢查並整理</button>';$('#settings .feature-grid').appendChild(liveTools);$('#previewLiveMigration').onclick=async()=>{const r=await fetch('/api/live-migration'),x=await r.json();$('#liveMigrationSummary').textContent=`找到 ${x.total} 組可整理到 LIVE 資料夾的舊照片。`;if(x.total&&confirm(`找到 ${x.total} 組 Live Photos。系統會先備份索引，再將每組照片與 MOV 一起移到 LIVE/年份/日期。確定開始？`))action('migrate-live')};
const liveOption=document.createElement('option');liveOption.value='LIVE';liveOption.textContent='Live Photos';$('#galleryType').appendChild(liveOption);

let photoMap=null,mapPhotoLayer=null,mapBounds=null,mapItems=[];
let mapTotal=0,mapOffset=0;
const mapMore=document.createElement('button');mapMore.className='load-more';mapMore.hidden=true;mapMore.textContent='載入更多拍攝地點';$('#map').appendChild(mapMore);
function ensurePhotoMap(){
  if(photoMap)return;
  photoMap=L.map('photoMap',{zoomControl:true,minZoom:2,worldCopyJump:true});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
  }).addTo(photoMap);
  mapPhotoLayer=L.layerGroup().addTo(photoMap);
  photoMap.setView([23.7,121],7);
}
function fitPhotoMap(){
  if(!photoMap)return;
  photoMap.invalidateSize();
  if(mapBounds?.isValid()){
    if(mapItems.length===1)photoMap.setView(mapBounds.getCenter(),15);
    else photoMap.fitBounds(mapBounds,{padding:[55,55],maxZoom:15});
  }
}
function mapMarkerIcon(item,count){
  const name=item.current_path.split(/[\\/]/).pop();
  return L.divIcon({
    className:'photo-map-icon',
    html:`<div class="photo-map-marker" title="${esc(name)}"><img src="/api/thumbnail/${item.id}?session=${mediaEpoch}" alt="">${count>1?`<span>${count}</span>`:''}</div>`,
    iconSize:[54,54],iconAnchor:[27,27],popupAnchor:[0,-31]
  });
}
function groupMapItems(items){
  const groups=new Map();
  items.forEach(item=>{
    const key=`${Number(item.gps_latitude).toFixed(5)},${Number(item.gps_longitude).toFixed(5)}`;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(item);
  });
  return [...groups.values()];
}
function mapGroupPopup(items){
  const wrap=document.createElement('div');wrap.className='map-photo-popup';
  const heading=document.createElement('b');heading.textContent=`這個地點有 ${items.length} 張照片`;wrap.appendChild(heading);
  const grid=document.createElement('div');
  wrap.appendChild(grid);
  let offset=0;
  const controls=document.createElement('footer'),prev=document.createElement('button'),next=document.createElement('button'),label=document.createElement('small');
  prev.textContent='上一頁';next.textContent='下一頁';controls.append(prev,label,next);wrap.appendChild(controls);
  function show(){
    grid.replaceChildren();
    items.slice(offset,offset+30).forEach(item=>{const name=item.current_path.split(/[\\/]/).pop(),button=document.createElement('button');button.type='button';button.title=name;button.innerHTML=`<img loading="lazy" src="/api/thumbnail/${item.id}?session=${mediaEpoch}" alt="${esc(name)}">`;button.onclick=()=>{galleryItems=items;openPhoto(item,name)};grid.appendChild(button)});
    label.textContent=`${Math.floor(offset/30)+1} / ${Math.ceil(items.length/30)}`;prev.disabled=offset===0;next.disabled=offset+30>=items.length;
  }
  prev.onclick=()=>{offset-=30;show()};next.onclick=()=>{offset+=30;show()};show();
  return wrap;
}
loadMap=async(more=false)=>{
  ensurePhotoMap();
  if(!more){mapOffset=0;mapItems=[]}
  mapMore.disabled=true;
  $('#mapCount').textContent='正在讀取具有 GPS 的照片…';
  try{
    const r=await fetch(`/api/map?offset=${mapOffset}`);if(!r.ok)throw Error('無法讀取拍攝位置');
    const x=await r.json();mapItems.push(...x.items);mapOffset+=x.items.length;mapTotal=x.total;galleryItems=mapItems;
    mapPhotoLayer.clearLayers();mapBounds=L.latLngBounds([]);
    groupMapItems(mapItems).forEach(items=>{
      const first=items[0],lat=Number(first.gps_latitude),lng=Number(first.gps_longitude),marker=L.marker([lat,lng],{icon:mapMarkerIcon(first,items.length),title:first.current_path.split(/[\\/]/).pop(),riseOnHover:true}).addTo(mapPhotoLayer);
      mapBounds.extend([lat,lng]);
      if(items.length===1)marker.on('click',()=>{galleryItems=mapItems;openPhoto(first,first.current_path.split(/[\\/]/).pop())});
      else marker.bindPopup(()=>mapGroupPopup(items),{maxWidth:320,className:'map-photo-popup-shell'});
    });
    $('#mapCount').textContent=`已載入 ${mapItems.length} / ${mapTotal} 張位置照片 · 點照片可放大查看`;
    mapMore.hidden=mapOffset>=mapTotal;
    $('#mapEmpty').hidden=mapItems.length>0;$('#mapFit').hidden=mapItems.length===0;
    requestAnimationFrame(()=>setTimeout(fitPhotoMap,80));
  }catch(e){$('#mapCount').textContent=e.message;$('#mapEmpty').hidden=mapItems.length>0;$('#mapFit').hidden=!mapItems.length}
  finally{mapMore.disabled=false}
};
$('#mapFit').onclick=fitPhotoMap;
mapMore.onclick=()=>loadMap(true);

let albumId=null,albumOffset=0,albumTotal=0,albumItems=[];
const albumMore=document.createElement('button');albumMore.className='load-more';albumMore.textContent='載入更多相簿照片';albumMore.hidden=true;$('#albums').appendChild(albumMore);
async function loadAlbumPage(id,reset=false){
  if(reset){albumId=id;albumOffset=0;albumItems=[];$('#albumGrid').replaceChildren()}
  const requested=id;albumMore.disabled=true;
  try{
    const r=await fetch(`/api/album/${id}?offset=${albumOffset}`);if(!r.ok)throw Error('無法讀取相簿');
    const x=await r.json();if(albumId!==requested)return;
    albumItems.push(...x.items);galleryItems=albumItems;albumOffset+=x.items.length;albumTotal=x.total;
    x.items.forEach(item=>{const card=document.createElement('article'),name=item.current_path.split(/[\\/]/).pop();card.className='photo-card';card.tabIndex=0;card.innerHTML=`<img loading="lazy" src="/api/thumbnail/${item.id}?session=${mediaEpoch}" alt="${esc(name)}"><div><b>${esc(name)}</b></div>`;card.onclick=()=>{galleryItems=albumItems;openPhoto(item,name)};card.onkeydown=e=>{if(e.key==='Enter')card.click()};$('#albumGrid').appendChild(card)});
    if(!albumItems.length)$('#albumGrid').innerHTML='<article>相簿目前沒有照片</article>';
    albumMore.hidden=albumOffset>=albumTotal;albumMore.textContent=`載入更多（${albumOffset} / ${albumTotal}）`;
  }catch(e){toast(e.message)}finally{albumMore.disabled=false}
}
$('#albumGrid').onclick=e=>{const button=e.target.closest('[data-album]');if(button)loadAlbumPage(Number(button.dataset.album),true)};
albumMore.onclick=()=>loadAlbumPage(albumId);
const baseLoadAlbums=loadAlbums;loadAlbums=async()=>{albumId=null;albumMore.hidden=true;await baseLoadAlbums()};
let trashOffset=0;
const trashMore=document.createElement('button');trashMore.className='load-more';trashMore.textContent='載入更多回收站項目';trashMore.hidden=true;$('#trash').appendChild(trashMore);
loadTrash=async(more=false)=>{
  if(!more){trashOffset=0;$('#trashList').replaceChildren()}
  trashMore.disabled=true;
  try{
    const r=await fetch(`/api/trash?offset=${trashOffset}`);if(!r.ok)throw Error('無法讀取回收站');const x=await r.json();
    $('#trashList').insertAdjacentHTML('beforeend',x.items.map(i=>`<label class="trash-row"><input type="checkbox" value="${i.id}"><b>${esc(i.current_path.split(/[\\/]/).pop())}</b><small>${esc((i.deleted_at||'').slice(0,19).replace('T',' '))}</small></label>`).join(''));
    trashOffset+=x.items.length;trashMore.hidden=trashOffset>=x.total;trashMore.textContent=`載入更多（${trashOffset} / ${x.total}）`;
    if(!trashOffset)$('#trashList').innerHTML='<div class="empty">回收站是空的</div>';
  }catch(e){toast(e.message)}finally{trashMore.disabled=false}
};
trashMore.onclick=()=>loadTrash(true);
const purgeChoice=document.createElement('label');
purgeChoice.innerHTML='<input type="checkbox" id="autoPurge"> 自動永久刪除回收站過期項目（預設關閉）';
$('#saveSettings').before(purgeChoice);
const loadSettingsBeforePurgeChoice=loadSettings;
loadSettings=async()=>{await loadSettingsBeforePurgeChoice();const r=await fetch('/api/settings'),x=await r.json();$('#autoPurge').checked=String(x.settings?.auto_purge).toLowerCase()==='true'};
