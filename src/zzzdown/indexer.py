#!/usr/bin/env python3

import html
import json
import plistlib
import re
import secrets
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def find_sibling(base: Path, extensions: tuple[str, ...], video_id: str = "") -> Path | None:
    candidates = []
    for extension in extensions:
        candidate = base.with_suffix(extension)
        if candidate.exists():
            candidates.append(candidate)
    candidates.extend(
        p for p in base.parent.glob(base.name + ".*")
        if p.suffix.lower() in extensions and p not in candidates
    )
    # yt-dlp 按字节截断长标题时，元数据名和合并后的视频名可能截在不同位置。
    # 平台视频 ID 不受截断影响，因此用它作可靠的第二匹配键。
    if not candidates and video_id:
        marker = f"[{video_id}]"
        candidates.extend(
            p for p in base.parent.iterdir()
            if p.is_file() and p.suffix.lower() in extensions and marker in p.name
        )
    if not candidates:
        return None
    # 若旧版 MP4 与新下载的 MKV 同时存在，索引优先使用最新生成的高画质文件。
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.stat().st_size))


def format_duration(seconds) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def display_date(raw: str) -> str:
    raw = str(raw or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def relative_url(path: Path, root: Path) -> str:
    return quote(path.relative_to(root).as_posix(), safe="/()[]!$&'*,;=@-._~")


def site_name(source_dir: str, original: str) -> str:
    source = source_dir.lower()
    host = urlparse(original).netloc.lower()
    if "bilibili" in source or "bilibili" in host:
        return "哔哩哔哩"
    if "xinpianchang" in source or "xinpianchang" in host:
        return "新片场"
    if "youtube" in source or "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "douyin" in source or "douyin.com" in host:
        return "抖音"
    if "tiktok" in source or "tiktok.com" in host:
        return "TikTok"
    return source_dir or host.replace("www.", "") or "其他网站"


def task_type(source_dir: str, source_url: str, data: dict) -> str:
    source = source_dir.lower()
    url = source_url.lower()
    if (
        "collection" in source
        or "playlist" in source
        or "list=" in url
        or "/lists/" in url
        or "/playlist" in url
        or data.get("playlist")
        or data.get("playlist_title")
    ):
        return "合集/播放列表"
    if (
        re.search(r"xinpianchang\.com/u\d+", url)
        or "space.bilibili.com" in url
        or re.search(r"(?:youtube\.com|tiktok\.com)/@", url)
        or re.search(r"douyin\.com/user/", url)
    ):
        return "创作者主页"
    return "单个视频"


def load_items(root: Path, include_collection: bool = False) -> list[dict]:
    items = []
    for info_path in root.rglob("*.info.json"):
        if "视频库回收站" in info_path.relative_to(root).parts:
            continue
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        base = Path(str(info_path)[:-len(".info.json")])
        video_id = str(data.get("id") or "")
        video = find_sibling(base, VIDEO_EXTENSIONS, video_id)
        if not video:
            continue
        video_stat = video.stat()
        downloaded_at = datetime.fromtimestamp(video_stat.st_mtime)
        image = find_sibling(base, IMAGE_EXTENSIONS, video_id)
        upload_date = str(data.get("upload_date") or "")
        title = str(data.get("title") or video.stem)
        original = str(data.get("webpage_url") or data.get("original_url") or "")
        relative_parts = info_path.relative_to(root).parts
        source_dir = relative_parts[0] if include_collection and len(relative_parts) > 1 else ""
        task_name = relative_parts[1] if include_collection and len(relative_parts) > 2 else root.name
        task_dir = root / source_dir / task_name if include_collection else root
        try:
            source_url = (task_dir / ".source-url.txt").read_text(encoding="utf-8").strip()
        except OSError:
            source_url = original
        platform = site_name(source_dir, original)
        kind = task_type(source_dir, source_url, data)
        task_metadata = data.get("zzzdown") if isinstance(data.get("zzzdown"), dict) else {}
        task_id = str(task_metadata.get("task_id") or f"legacy::{source_dir}/{task_name}")
        try:
            task_started_timestamp = int(task_metadata.get("started_at") or 0)
        except (TypeError, ValueError):
            task_started_timestamp = 0
        try:
            download_batch_index = max(1, int(task_metadata.get("batch_index") or 1))
        except (TypeError, ValueError):
            download_batch_index = 1
        try:
            download_item_index = max(
                0,
                int(
                    task_metadata.get("item_index")
                    or data.get("playlist_index")
                    or data.get("playlist_autonumber")
                    or 0
                ),
            )
        except (TypeError, ValueError):
            download_item_index = 0
        heights = [data.get("height")]
        heights.extend(
            item.get("height") for item in (data.get("requested_formats") or [])
            if isinstance(item, dict)
        )
        height = max((int(value) for value in heights if isinstance(value, (int, float))), default=0)
        duplicate_key = f"{platform.lower()}::{video_id}" if video_id else f"file::{video.name.lower()}"
        items.append({
            "title": title,
            "date": display_date(upload_date),
            "sortDate": upload_date,
            "downloadDate": downloaded_at.strftime("%Y-%m-%d"),
            "downloadTimestamp": int(video_stat.st_mtime * 1000),
            "duration": format_duration(data.get("duration")),
            "id": video_id,
            "video": relative_url(video, root),
            "videoPath": video.relative_to(root).as_posix(),
            "fileSize": video_stat.st_size,
            "height": height,
            "thumbnail": relative_url(image, root) if image else "",
            "original": original,
            "description": str(data.get("description") or "")[:500],
            "collection": " / ".join(relative_parts[:2]) if include_collection else "",
            "site": platform,
            "taskType": kind,
            "task": task_name,
            "taskUrl": source_url,
            "downloadTaskId": task_id,
            "downloadTaskStartedTimestamp": task_started_timestamp,
            "downloadBatchIndex": download_batch_index,
            "downloadItemIndex": download_item_index,
            "duplicateKey": duplicate_key,
        })
    task_started = {}
    for item in items:
        task_started[item["downloadTaskId"]] = max(
            task_started.get(item["downloadTaskId"], 0),
            item["downloadTaskStartedTimestamp"] or item["downloadTimestamp"],
        )
    for item in items:
        item["downloadTaskStartedTimestamp"] = task_started[item["downloadTaskId"]]
    duplicate_counts = Counter(item["duplicateKey"] for item in items)
    for item in items:
        item["duplicateCount"] = duplicate_counts[item["duplicateKey"]]
    items.sort(key=lambda item: (item["sortDate"], item["title"]), reverse=True)
    return items


def build_html(up_name: str, items: list[dict], management_token: str = "") -> str:
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    token_payload = json.dumps(management_token)
    safe_name = html.escape(up_name)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_name} · 本地视频库</title>
<style>
:root {{ color-scheme: light; --pink:#fb7299; --ink:#18191c; --muted:#6d757a; --line:#e3e5e7; --bg:#f6f7f8; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; color:var(--ink); background:var(--bg); }}
header {{ position:sticky; top:0; z-index:2; padding:24px max(24px,calc((100vw - 1280px)/2)); background:rgba(255,255,255,.94); backdrop-filter:blur(14px); border-bottom:1px solid var(--line); }}
h1 {{ margin:0 0 6px; font-size:26px; }}
.summary {{ color:var(--muted); font-size:14px; }}
.toolbar {{ display:grid; grid-template-columns:minmax(260px,1fr) auto auto auto; gap:12px; margin-top:18px; }}
.search {{ width:100%; padding:13px 16px; border:1px solid var(--line); border-radius:12px; font-size:16px; outline:none; background:#fff; }}
.search:focus {{ border-color:var(--pink); box-shadow:0 0 0 3px rgba(251,114,153,.14); }}
.filters {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:12px; }}
.filter-label {{ margin-right:2px; color:var(--muted); font-size:13px; }}
.chip,.duplicate-toggle,.clean-all,.recycle-button {{ appearance:none; padding:7px 11px; border:1px solid var(--line); border-radius:999px; color:var(--ink); background:#fff; cursor:pointer; font-size:13px; }}
.chip:hover,.duplicate-toggle:hover {{ border-color:#aeb3b7; }}
.chip.active,.duplicate-toggle.active {{ border-color:var(--pink); color:#d94d79; background:#fff2f6; }}
.duplicate-toggle {{ white-space:nowrap; }}
.clean-all {{ border-color:#f0b4c6; color:#b42e59; background:#fff3f6; white-space:nowrap; }}
.clean-all:hover {{ background:#ffe5ed; }} .clean-all:disabled {{ opacity:.55; cursor:wait; }}
.recycle-button {{ white-space:nowrap; }} .recycle-button:hover {{ border-color:#8d969c; background:#f5f6f7; }}
.filters.channels .chip {{ min-width:112px; padding:9px 14px; border-radius:10px; font-weight:600; }}
main {{ max-width:1280px; margin:0 auto; padding:24px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:20px; }}
.card {{ position:relative; overflow:hidden; border:1px solid var(--line); border-radius:14px; background:#fff; box-shadow:0 4px 18px rgba(0,0,0,.04); }}
.card.duplicate {{ border-color:#f4b5c9; }}
.cover {{ position:relative; display:block; aspect-ratio:16/9; background:#e7e9eb; overflow:hidden; }}
.cover img {{ width:100%; height:100%; object-fit:cover; transition:transform .2s ease; }}
.cover:hover img {{ transform:scale(1.03); }}
.placeholder {{ display:grid; place-items:center; width:100%; height:100%; color:var(--muted); }}
.duration {{ position:absolute; right:8px; bottom:8px; padding:3px 7px; border-radius:5px; color:#fff; background:rgba(0,0,0,.68); font-size:12px; }}
.body {{ padding:13px 14px 15px; }}
.badges {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }}
.badge {{ padding:3px 7px; border-radius:6px; color:#526069; background:#f0f2f3; font-size:11px; }}
.badge.site {{ color:#0d6670; background:#e9f7f8; }}
.badge.duplicate {{ color:#b42e59; background:#fff0f4; }}
.task {{ margin-bottom:5px; overflow:hidden; color:var(--muted); font-size:12px; text-overflow:ellipsis; white-space:nowrap; }}
.title {{ display:-webkit-box; min-height:44px; overflow:hidden; color:var(--ink); text-decoration:none; font-weight:600; line-height:1.4; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.title:hover {{ color:var(--pink); }}
.meta {{ display:flex; justify-content:space-between; gap:8px; margin-top:10px; color:var(--muted); font-size:12px; }}
.original {{ color:var(--muted); text-decoration:none; }} .original:hover {{ color:var(--pink); }}
.actions {{ display:flex; justify-content:flex-end; margin-top:10px; }}
.trash {{ padding:6px 9px; border:1px solid #f0b4c6; border-radius:8px; color:#b42e59; background:#fff7f9; cursor:pointer; font-size:12px; }}
.trash:hover {{ background:#ffeaf0; }} .trash:disabled {{ opacity:.55; cursor:wait; }}
.notice {{ margin:0 0 18px; padding:12px 14px; border:1px solid #f0d695; border-radius:10px; color:#72551b; background:#fff9e9; font-size:13px; }}
.empty {{ padding:80px 20px; text-align:center; color:var(--muted); }}
.recycle-dialog {{ width:min(980px,calc(100vw - 32px)); max-height:82vh; padding:0; border:1px solid var(--line); border-radius:18px; background:var(--bg); box-shadow:0 24px 80px rgba(0,0,0,.24); }}
.recycle-dialog::backdrop {{ background:rgba(20,25,28,.42); backdrop-filter:blur(3px); }}
.dialog-head {{ position:sticky; top:0; z-index:1; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:18px 20px; border-bottom:1px solid var(--line); background:rgba(255,255,255,.96); }}
.dialog-head h2 {{ margin:0; font-size:21px; }}
.dialog-actions {{ display:flex; gap:8px; }}
.restore-all,.dialog-close,.restore-one,.empty-trash {{ padding:8px 11px; border:1px solid var(--line); border-radius:9px; background:#fff; cursor:pointer; }}
.restore-all,.restore-one {{ border-color:#99cdbd; color:#18705a; background:#f0fbf7; }}
.restore-all:hover,.restore-one:hover {{ background:#dff6ed; }}
.empty-trash {{ border-color:#efb0b0; color:#a82c2c; background:#fff3f3; }} .empty-trash:hover {{ background:#ffe3e3; }}
.trash-list {{ display:grid; gap:10px; padding:18px; }}
.trash-item {{ display:grid; grid-template-columns:144px 1fr auto; gap:14px; align-items:center; padding:10px; border:1px solid var(--line); border-radius:12px; background:#fff; }}
.trash-thumb {{ width:144px; aspect-ratio:16/9; object-fit:cover; border-radius:8px; background:#e7e9eb; }}
.trash-title {{ margin-bottom:5px; font-weight:650; }}
.trash-meta {{ color:var(--muted); font-size:12px; line-height:1.6; }}
.trash-empty {{ padding:60px 20px; text-align:center; color:var(--muted); }}
@media (max-width:700px) {{ header {{ padding:18px; }} .toolbar {{ grid-template-columns:1fr; }} main {{ padding:16px; }} .grid {{ grid-template-columns:1fr 1fr; gap:10px; }} .body {{ padding:10px; }} }}
</style>
</head>
<body>
<header>
  <h1>{safe_name} · 本地视频库</h1>
  <div class="summary"><span id="count"></span> 个视频 · 索引更新于 {generated}</div>
  <div class="toolbar"><input id="search" class="search" type="search" placeholder="搜索标题、创作者、任务或 BV/AV 号…" autofocus><button id="duplicates" class="duplicate-toggle" type="button">只看重复项</button><button id="cleanAll" class="clean-all" type="button">一键清理全部重复项</button><button id="recycleBin" class="recycle-button" type="button">回收站</button></div>
  <div id="siteFilters" class="filters channels"></div>
  <div id="typeFilters" class="filters"></div>
  <div id="taskFilters" class="filters"></div>
</header>
<main><div id="managementNotice" class="notice" hidden>清理功能需要通过「打开全局视频库.command」启动本地管理服务。</div><div id="grid" class="grid"></div><div id="empty" class="empty" hidden>没有找到匹配的视频</div></main>
<dialog id="recycleDialog" class="recycle-dialog"><div class="dialog-head"><div><h2>视频库回收站</h2><div id="trashSummary" class="summary"></div></div><div class="dialog-actions"><button id="restoreAll" class="restore-all" type="button">全部还原</button><button id="emptyTrash" class="empty-trash" type="button">清空回收站</button><button id="closeTrash" class="dialog-close" type="button">关闭</button></div></div><div id="trashList" class="trash-list"></div></dialog>
<script>
let items = {payload};
const managementToken = {token_payload};
const grid = document.getElementById('grid');
const empty = document.getElementById('empty');
const count = document.getElementById('count');
const apiBase = location.protocol === 'http:' ? '' : 'http://127.0.0.1:8765';
const state = {{site:'全部渠道', taskType:'全部类型', task:'全部任务', duplicates:false, query:''}};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const formatBytes = n => n > 1073741824 ? `${{(n/1073741824).toFixed(1)}} GB` : `${{Math.max(1,n/1048576).toFixed(0)}} MB`;
async function apiRequest(path, options={{}}) {{
  const headers={{'X-Video-Library-Token':managementToken,...(options.headers||{{}})}};
  const response=await fetch(`${{apiBase}}${{path}}`,{{...options,headers}});
  const payload=await response.json();
  if(!response.ok) throw new Error(payload.error||'视频库操作失败');
  return payload;
}}
function makeFilters(id, label, values, key, allLabel) {{
  const target = document.getElementById(id);
  target.innerHTML = `<span class="filter-label">${{label}}</span>` + [allLabel, ...values].map(value => `<button type="button" class="chip ${{state[key]===value?'active':''}}" data-filter="${{key}}" data-value="${{esc(value)}}">${{esc(value)}}${{value===allLabel?'':` · ${{items.filter(x=>x[key]===value).length}}`}}</button>`).join('');
}}
function render() {{
  const q = state.query.trim().toLocaleLowerCase();
  const shown = items.filter(x =>
    (state.site === '全部渠道' || x.site === state.site) &&
    (state.taskType === '全部类型' || x.taskType === state.taskType) &&
    (state.task === '全部任务' || x.task === state.task) &&
    (!state.duplicates || x.duplicateCount > 1) &&
    (!q || `${{x.title}} ${{x.date}} ${{x.id}} ${{x.description}} ${{x.task}} ${{x.site}} ${{x.taskType}}`.toLocaleLowerCase().includes(q))
  );
  count.textContent = shown.length === items.length ? items.length : `${{shown.length}} / ${{items.length}}`;
  grid.innerHTML = shown.map(x => `<article class="card ${{x.duplicateCount>1?'duplicate':''}}" data-path="${{esc(x.videoPath)}}">
    <a class="cover" href="${{x.video}}" target="_blank">${{x.thumbnail ? `<img src="${{x.thumbnail}}" loading="lazy" alt="">` : '<span class="placeholder">暂无封面</span>'}}${{x.duration ? `<span class="duration">${{esc(x.duration)}}</span>` : ''}}</a>
    <div class="body"><div class="badges"><span class="badge site">${{esc(x.site)}}</span><span class="badge">${{esc(x.taskType)}}</span>${{x.duplicateCount>1?`<span class="badge duplicate">重复 ${{x.duplicateCount}} 份</span>`:''}}</div><div class="task" title="${{esc(x.task)}}">任务：${{esc(x.task)}}</div><a class="title" href="${{x.video}}" target="_blank">${{esc(x.title)}}</a>
    <div class="meta"><span>${{esc(x.date || '日期未知')}}${{x.height?` · ${{x.height}}P`:''}}</span><span>${{formatBytes(x.fileSize)}}</span>${{x.original ? `<a class="original" href="${{x.original}}" target="_blank">原网页 ↗</a>` : `<span>${{esc(x.id)}}</span>`}}</div>${{x.duplicateCount>1?`<div class="actions"><button class="trash" type="button" data-trash="${{esc(x.videoPath)}}">移到视频库回收站</button></div>`:''}}</div>
  </article>`).join('');
  empty.hidden = shown.length !== 0;
  updateCleanButton();
}}
makeFilters('siteFilters', '渠道', [...new Set(items.map(x=>x.site))], 'site', '全部渠道');
makeFilters('typeFilters', '任务类型', ['创作者主页','合集/播放列表','单个视频'].filter(v=>items.some(x=>x.taskType===v)), 'taskType', '全部类型');
function matchingTasks() {{ return [...new Set(items.filter(x=>(state.site==='全部渠道'||x.site===state.site)&&(state.taskType==='全部类型'||x.taskType===state.taskType)).map(x=>x.task))]; }}
function bindFilters() {{ document.querySelectorAll('[data-filter]').forEach(button => button.onclick=()=>{{ const key=button.dataset.filter; state[key]=button.dataset.value; if(key==='site'||key==='taskType') state.task='全部任务'; refreshFilters(); render(); }}); }}
function refreshFilters() {{ makeFilters('siteFilters', '渠道', [...new Set(items.map(x=>x.site))], 'site', '全部渠道'); makeFilters('typeFilters', '任务类型', ['创作者主页','合集/播放列表','单个视频'].filter(v=>items.some(x=>x.taskType===v)), 'taskType', '全部类型'); makeFilters('taskFilters', '下载任务', matchingTasks(), 'task', '全部任务'); bindFilters(); }}
makeFilters('taskFilters', '下载任务', matchingTasks(), 'task', '全部任务');
bindFilters();
document.getElementById('search').addEventListener('input', e => {{state.query=e.target.value; render();}});
document.getElementById('duplicates').addEventListener('click', e => {{state.duplicates=!state.duplicates; e.currentTarget.classList.toggle('active',state.duplicates); render();}});
let trashItems=[];
function renderTrash() {{
  const list=document.getElementById('trashList');
  document.getElementById('trashSummary').textContent=`${{trashItems.length}} 个可还原视频`;
  document.getElementById('recycleBin').textContent=`回收站${{trashItems.length?` · ${{trashItems.length}}`:''}}`;
  document.getElementById('restoreAll').hidden=trashItems.length===0;
  document.getElementById('emptyTrash').hidden=trashItems.length===0;
  list.innerHTML=trashItems.length?trashItems.map(x=>`<article class="trash-item"><div>${{x.thumbnail?`<img class="trash-thumb" src="${{x.thumbnail}}" alt="">`:'<div class="trash-thumb placeholder">暂无封面</div>'}}</div><div><div class="trash-title">${{esc(x.title)}}</div><div class="trash-meta">原任务：${{esc(x.task)}}<br>删除时间：${{esc(x.deletedAt)}} · ${{formatBytes(x.fileSize)}}</div></div><button class="restore-one" type="button" data-restore="${{esc(x.trashVideoPath)}}">还原</button></article>`).join(''):'<div class="trash-empty">回收站是空的</div>';
}}
async function loadTrash(showErrors=false) {{
  if(!managementToken) return;
  try {{ const payload=await apiRequest('/api/trash-items'); trashItems=payload.items||[]; renderTrash(); }}
  catch(error) {{ if(showErrors) alert(`${{error.message}}\n\n请重新双击「打开全局视频库.command」。`); }}
}}
document.getElementById('recycleBin').addEventListener('click',async()=>{{ await loadTrash(true); document.getElementById('recycleDialog').showModal(); }});
document.getElementById('closeTrash').addEventListener('click',()=>document.getElementById('recycleDialog').close());
document.getElementById('trashList').addEventListener('click',async event=>{{
  const button=event.target.closest('[data-restore]'); if(!button) return;
  const item=trashItems.find(x=>x.trashVideoPath===button.dataset.restore); if(!item||!confirm(`将「${{item.title}}」还原到原任务目录吗？`)) return;
  button.disabled=true;
  try {{ await apiRequest('/api/restore',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{trashVideoPath:item.trashVideoPath}})}}); location.assign(`http://127.0.0.1:8765/?restored=${{Date.now()}}`); }}
  catch(error) {{ alert(error.message); button.disabled=false; }}
}});
document.getElementById('restoreAll').addEventListener('click',async event=>{{
  if(!trashItems.length||!confirm(`确定将回收站中的 ${{trashItems.length}} 个视频全部还原吗？`)) return;
  const button=event.currentTarget; button.disabled=true; button.textContent='正在还原…';
  try {{ await apiRequest('/api/restore',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{trashVideoPaths:trashItems.map(x=>x.trashVideoPath)}})}}); location.assign(`http://127.0.0.1:8765/?restored=${{Date.now()}}`); }}
  catch(error) {{ alert(error.message); button.disabled=false; button.textContent='全部还原'; }}
}});
document.getElementById('emptyTrash').addEventListener('click',async event=>{{
  if(!trashItems.length) return;
  const total=trashItems.reduce((sum,x)=>sum+x.fileSize,0);
  const confirmation=prompt(`此操作会永久删除回收站中的 ${{trashItems.length}} 个视频（约 ${{formatBytes(total)}}），无法恢复。\n\n请输入“清空”继续：`);
  if(confirmation!=='清空') return;
  const button=event.currentTarget; button.disabled=true; button.textContent='正在永久删除…';
  try {{
    const result=await apiRequest('/api/empty-trash',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{confirm:'清空'}})}});
    trashItems=[]; renderTrash(); alert(`已永久删除 ${{result.deletedVideos}} 个视频。`);
  }} catch(error) {{ alert(error.message); }} finally {{ button.disabled=false; button.textContent='清空回收站'; }}
}});
function cleanupCandidates() {{
  const groups=new Map();
  for(const item of items) {{ if(!groups.has(item.duplicateKey)) groups.set(item.duplicateKey,[]); groups.get(item.duplicateKey).push(item); }}
  const remove=[];
  for(const group of groups.values()) {{
    if(group.length<2) continue;
    group.sort((a,b)=>(b.height-a.height)||(b.fileSize-a.fileSize)||String(b.sortDate).localeCompare(String(a.sortDate)));
    remove.push(...group.slice(1));
  }}
  return remove;
}}
function updateDuplicateCounts() {{ const counts={{}}; items.forEach(x=>counts[x.duplicateKey]=(counts[x.duplicateKey]||0)+1); items.forEach(x=>x.duplicateCount=counts[x.duplicateKey]); }}
function updateCleanButton() {{ const candidates=cleanupCandidates(); const button=document.getElementById('cleanAll'); button.hidden=candidates.length===0; button.textContent=candidates.length?`一键清理 ${{candidates.length}} 个重复文件`:'没有重复文件'; }}
document.getElementById('cleanAll').addEventListener('click', async event => {{
  const candidates=cleanupCandidates();
  if(!candidates.length) return;
  const total=candidates.reduce((sum,x)=>sum+x.fileSize,0);
  if(!confirm(`将自动保留每组中分辨率最高、文件最大的一份，并把其余 ${{candidates.length}} 个重复视频（约 ${{formatBytes(total)}}）移到视频库回收站。\n\n是否继续？`)) return;
  if(!managementToken) {{ document.getElementById('managementNotice').hidden=false; return; }}
  const button=event.currentTarget; button.disabled=true; button.textContent='正在清理…';
  try {{
    const base=location.protocol==='http:'?'':'http://127.0.0.1:8765';
    const response=await fetch(`${{base}}/api/trash`,{{method:'POST',headers:{{'Content-Type':'application/json','X-Video-Library-Token':managementToken}},body:JSON.stringify({{videoPaths:candidates.map(x=>x.videoPath)}})}});
    if(!response.ok) throw new Error((await response.json()).error||'清理失败');
    const removed=new Set(candidates.map(x=>x.videoPath)); items=items.filter(x=>!removed.has(x.videoPath)); updateDuplicateCounts(); refreshFilters(); render();
    alert(`已将 ${{candidates.length}} 个重复视频移到视频库回收站。`);
  }} catch(error) {{ document.getElementById('managementNotice').hidden=false; alert(`${{error.message}}\n\n请双击「打开全局视频库.command」后再试。`); }} finally {{ button.disabled=false; updateCleanButton(); }}
}});
grid.addEventListener('click', async event => {{
  const button = event.target.closest('[data-trash]');
  if (!button) return;
  if (!managementToken) {{ document.getElementById('managementNotice').hidden=false; return; }}
  const item = items.find(x=>x.videoPath===button.dataset.trash);
  if (!item || !confirm(`确定将这份重复视频及封面、字幕、元数据移到视频库回收站吗？\n\n${{item.title}}\n${{item.task}}`)) return;
  button.disabled=true; button.textContent='正在移动…';
  try {{
    const base = location.protocol==='http:' ? '' : 'http://127.0.0.1:8765';
    const response = await fetch(`${{base}}/api/trash`, {{method:'POST',headers:{{'Content-Type':'application/json','X-Video-Library-Token':managementToken}},body:JSON.stringify({{videoPath:item.videoPath}})}});
    if (!response.ok) throw new Error((await response.json()).error || '清理失败');
    items = items.filter(x=>x.videoPath!==item.videoPath); updateDuplicateCounts();
    refreshFilters(); render();
  }} catch (error) {{ document.getElementById('managementNotice').hidden=false; alert(`${{error.message}}\n\n请双击「打开全局视频库.command」后再试。`); button.disabled=false; button.textContent='移到视频库回收站'; }}
}});
render();
if(managementToken) loadTrash(); else document.getElementById('recycleBin').hidden=true;
</script>
</body>
</html>'''


ENGLISH_REPLACEMENTS = {
    'lang="zh-CN"': 'lang="en"',
    " · 本地视频库": " · Local Video Library",
    "个视频 · 索引更新于": "videos · Updated",
    "搜索标题、创作者、任务或 BV/AV 号…": "Search title, creator, task, or BV/AV ID…",
    "只看重复项": "Duplicates only",
    "清理重复项": "Clean duplicates",
    "视频库回收站": "Library Recycle Bin",
    "回收站": "Recycle Bin",
    "自定义文件夹": "Custom folders",
    "＋ 新建": "+ New",
    "选择视频": "Select video",
    "移动到文件夹": "Move to folder",
    "删除所选": "Delete selected",
    "取消选择": "Clear selection",
    "管理功能需要通过“打开全局视频库.command”启动。": "Management features are available when the library is opened from ZZZDown.",
    "没有找到匹配的视频": "No matching videos",
    "全部还原": "Restore all",
    "清空回收站": "Empty recycle bin",
    "关闭": "Close",
    "新建文件夹": "New folder",
    "文件夹名称": "Folder name",
    "上级文件夹": "Parent folder",
    "取消": "Cancel",
    "保存": "Save",
    "移动到自定义文件夹": "Move to custom folder",
    "目标文件夹": "Destination folder",
    "请输入“清空”继续": "Type CLEAR to continue",
    "confirmation!=='清空'": "confirmation!=='CLEAR'",
    "confirm:'清空'": "confirm:'CLEAR'",
    "创作者主页": "Creator profile",
    "合集/播放列表": "Collection/playlist",
    "单个视频": "Single video",
    "全部渠道": "All sources",
    "全部类型": "All types",
    "全部任务": "All tasks",
    "下载日期": "Downloaded",
    "发布时间排序": "Published date",
    "下载任务顺序": "Download task order",
    "下载任务": "Download task",
    "视频排序": "Video sorting",
    "展开全部": "Show all",
    "收起": "Collapse",
    "全部日期": "All dates",
    "第 8–15 天": "Days 8–15",
    "第 16–30 天": "Days 16–30",
    "更早": "Older",
    "任务类型": "Task type",
    "渠道": "Source",
}


def build_library_html(up_name: str, items: list[dict], management_token: str = "", language: str = "zh_CN") -> str:
    template_path = Path(__file__).with_name("resources") / "视频库模板.html"
    template = template_path.read_text(encoding="utf-8")
    if language == "en_US":
        value_translations = {
            "哔哩哔哩": "Bilibili", "新片场": "Xinpianchang", "抖音": "Douyin",
            "其他网站": "Other", "创作者主页": "Creator profile",
            "合集/播放列表": "Collection/playlist", "单个视频": "Single video",
        }
        items = [
            {**item, "site": value_translations.get(item.get("site"), item.get("site")),
             "taskType": value_translations.get(item.get("taskType"), item.get("taskType"))}
            for item in items
        ]
        for source, translated in ENGLISH_REPLACEMENTS.items():
            template = template.replace(source, translated)
    replacements = {
        "__PAGE_TITLE__": html.escape(up_name),
        "__GENERATED_AT__": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "__ITEMS_JSON__": json.dumps(items, ensure_ascii=False).replace("</", "<\\/"),
        "__TOKEN_JSON__": json.dumps(management_token),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def generate_global(root: Path, shortcut_dir: Path | None = None) -> tuple[Path, int]:
    """Generate the managed ZZZDown library without spawning another process."""
    root = Path(root).resolve()
    shortcut_dir = Path(shortcut_dir or root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    shortcut_dir.mkdir(parents=True, exist_ok=True)
    items = load_items(root, include_collection=True)
    token_path = root / ".video-library-token"
    try:
        management_token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        management_token = ""
    if not management_token:
        management_token = secrets.token_urlsafe(32)
        token_path.write_text(management_token + "\n", encoding="utf-8")
        token_path.chmod(0o600)
    try:
        language = (root / ".zzzdown-language").read_text(encoding="utf-8").strip()
    except OSError:
        language = "zh_CN"
    index_path = root / "全局视频库.html"
    index_path.write_text(
        build_library_html("ZZZDown · Global Library" if language == "en_US" else "ZZZDown · 全局视频库", items, management_token, language),
        encoding="utf-8",
    )
    shortcut_path = shortcut_dir / "打开全局视频库.webloc"
    try:
        with shortcut_path.open("wb") as file:
            plistlib.dump({"URL": index_path.as_uri()}, file)
    except OSError:
        pass
    return index_path, len(items)


def main() -> None:
    global_mode = len(sys.argv) == 4 and sys.argv[1] == "--global"
    if global_mode:
        root = Path(sys.argv[2]).resolve()
        up_name = "全部网站 · 全局视频库"
        shortcut_dir = Path(sys.argv[3]).resolve()
    elif len(sys.argv) == 4:
        root = Path(sys.argv[1]).resolve()
        up_name = sys.argv[2]
        shortcut_dir = Path(sys.argv[3]).resolve()
    else:
        raise SystemExit("用法：生成视频索引.py <下载目录> <名称> <快捷链接目录>，或 --global <视频库> <快捷链接目录>")
    root.mkdir(parents=True, exist_ok=True)
    items = load_items(root, include_collection=global_mode)
    management_token = ""
    if global_mode:
        token_path = root / ".video-library-token"
        try:
            management_token = token_path.read_text(encoding="utf-8").strip()
        except OSError:
            management_token = ""
        if not management_token:
            management_token = secrets.token_urlsafe(32)
            token_path.write_text(management_token + "\n", encoding="utf-8")
            token_path.chmod(0o600)
    index_path = root / ("全局视频库.html" if global_mode else "视频索引.html")
    index_path.write_text(build_library_html(up_name, items, management_token), encoding="utf-8")

    shortcut_path = shortcut_dir / ("打开全局视频库.webloc" if global_mode else f"打开「{up_name}」视频库.webloc")
    with shortcut_path.open("wb") as f:
        plistlib.dump({"URL": index_path.as_uri()}, f)
    print(f"已收录 {len(items)} 个本地视频：{index_path}")


if __name__ == "__main__":
    main()
