const originalRenderWithProgress=render;
render=function(s){
  originalRenderWithProgress(s);
  $('#progressText').textContent=s.work_total?`已讀取 ${s.processed} / ${s.work_total}（${s.progress}%）`:s.busy?'正在建立檔案清單…':'尚未開始';
  $('#other').textContent=(s.counts.OTHER||0)+(s.counts.OTHER_DUPLICATE||0);
  $('#stop').hidden=!s.busy;
  const pages=Math.max(1,Math.ceil(s.total/500)),page=Math.floor(previewOffset/500)+1;
  $('#previewPage').textContent=`${Math.min(page,pages)} / ${pages}`;
  $('#previewPrev').disabled=previewOffset===0;
  $('#previewNext').disabled=previewOffset+500>=s.total;
  const storage=s.system.storage_mode;
  $('#storageMode').textContent=storage==='atomic'?`同磁碟區 · 原子搬移 · UID ${s.system.uid??'—'} / GID ${s.system.gid??'—'}`:storage==='verified-copy'?`跨磁碟區 · 複製驗證模式 · UID ${s.system.uid??'—'} / GID ${s.system.gid??'—'}`:'無法判斷儲存模式';
  (s.items||[]).forEach((item,index)=>{
    const image=$('#rows').children[index]?.querySelector('img.inbox-thumb');
    if(image)image.src=`/api/inbox-thumbnail/${previewOffset+index}?session=${mediaEpoch}`;
    if(item.file_type!=='OTHER')return;
    const row=$('#rows').children[index],cell=row?.children[1];
    if(cell)cell.innerHTML='<span class="file-icon">…</span>';
  });
};
$('#previewPrev').onclick=()=>{previewOffset=Math.max(0,previewOffset-500)};
$('#previewNext').onclick=()=>{if(lastSnapshot&&previewOffset+500<lastSnapshot.total)previewOffset+=500};
const originalScanClick=$('#scan').onclick;$('#scan').onclick=()=>{previewOffset=0;originalScanClick()};
$('#stop').onclick=()=>action('stop');
const originalImportClick=$('#import').onclick;
$('#import').onclick=()=>{
  const s=lastSnapshot,other=(s?.counts?.OTHER||0)+(s?.counts?.OTHER_DUPLICATE||0);
  if(!other)return originalImportClick();
  const gb=((s.total_bytes||0)/1073741824).toFixed(2);
  const message=`整理前確認\n\n總計：${s.total} 個檔案（${gb} GB）\n新照片：${s.counts.IMPORT||0}\n重複照片：${s.counts.DUPLICATE||0}\n沒有日期：${s.counts.UNSORTED||0}\n其他檔案：${other}\n\n其他檔案會移到安全隔離區；處理完成後會清除 Inbox 的空資料夾。`;
  if(confirm(message))action('import');
};
