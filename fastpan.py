import os
import io
import time
import zipfile
import secrets
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from dotenv import load_dotenv
from jinja2 import Template
from urllib.parse import quote

# ================== 配置 ==================
load_dotenv()

USERNAME = os.getenv("USERNAME", "admin")
PASSWORD = os.getenv("PASSWORD", "admin")
STORAGE = Path(os.getenv("STORAGE_DIR", "storage"))
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

STORAGE.mkdir(exist_ok=True)

TOKEN_TTL = 3600
TOKENS = {}
SHARES = {}

SECRET_KEY = os.getenv("SECRET_KEY", "fastpan-secret")
SHARE_FILE = STORAGE / "shares.json"

app = FastAPI()

# ================== 工具 ==================
def safe_path(rel: str) -> Path:
    rel = rel.lstrip("/")
    p = (STORAGE / rel).resolve()
    if not str(p).startswith(str(STORAGE.resolve())):
        raise ValueError("非法路径")
    return p

def is_login(req: Request) -> bool:
    t = req.cookies.get("token")
    return t in TOKENS and TOKENS[t] > time.time()

def human(size):
    for u in ["B","KB","MB","GB","TB"]:
        if size < 1024:
            return f"{size:.1f}{u}"
        size /= 1024
    return "?"

def dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

def zip_dir(path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in path.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(path))
    buf.seek(0)
    return buf

def hash_pw(pw: str) -> str:
    return hashlib.sha256((pw + SECRET_KEY).encode()).hexdigest()

def load_shares():
    global SHARES
    if SHARE_FILE.exists():
        try:
            with open(SHARE_FILE, "r", encoding="utf-8") as f:
                SHARES = json.load(f)
        except Exception:
            SHARES = {}
    # 清理过期
    now = time.time()
    changed = False
    for k in list(SHARES.keys()):
        exp = SHARES[k].get("exp")
        if exp and exp < now:
            del SHARES[k]
            changed = True
    if changed:
        save_shares()

def save_shares():
    tmp = SHARE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(SHARES, f, ensure_ascii=False)
    os.replace(tmp, SHARE_FILE)

# ================== HTML ==================
HTML = """<!doctype html>
<html class="h-full" x-data="{dark: localStorage.theme==='dark'}" :class="{'dark':dark}">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
<script>
tailwind.config = { darkMode:'class' }
</script>
<title>FastPan</title>
</head>
<body class="h-full bg-slate-100 dark:bg-slate-900 text-slate-800 dark:text-slate-100">

<div class="max-w-5xl mx-auto p-4">

<header class="flex justify-between items-center mb-6">
<h1 class="text-2xl font-bold text-blue-500">FastPan</h1>
<div class="flex gap-3">
<button onclick="toggleDark()"><i class="bi bi-moon"></i></button>
{% if not login %}
<button onclick="openLogin()" class="px-3 py-1 bg-blue-500 text-white rounded">登录</button>
{% else %}
<a href="/logout" class="text-sm text-slate-400">退出</a>
{% endif %}
</div>
</header>

<div class="text-sm text-slate-400 mb-2">
路径：
<a href="/" class="text-blue-500 hover:underline">/</a>
{% if path %}
  {% for seg in path.split('/') %}
    <span class="text-slate-400 mr-1"> </span>
    <a href="/?path={{ '/'.join(path.split('/')[:loop.index]) }}" class="text-blue-500 hover:underline">{{ seg }}{% if not loop.last %}/{% endif %}</a>
  {% endfor %}
{% endif %}
 ｜ 总大小：{{ total_size }}
</div>

{% if login %}
<!-- 上传 -->
<div class="bg-white dark:bg-slate-800 p-4 rounded-xl mb-4">
<input id="fileInput" type="file" class="hidden">
<div id="dropZone"
 class="border-2 border-dashed border-blue-300 p-6 text-center rounded-xl cursor-pointer">
<p id="fileHint"><i class="bi bi-cloud-arrow-up"></i> 点击或拖拽上传</p>
</div>
<button onclick="upload()" class="mt-3 w-full bg-blue-500 text-white py-2 rounded">上传</button>
<div class="h-2 bg-slate-200 rounded mt-2 overflow-hidden hidden" id="barWrap">
<div id="bar" class="h-full bg-blue-500 w-0"></div>
</div>
<button onclick="openMkdir()" class="mt-3 text-sm text-blue-500"><i class="bi bi-folder-plus"></i> 新建文件夹</button>
</div>
{% endif %}

<!-- 文件列表 -->
<div class="bg-white dark:bg-slate-800 rounded-xl divide-y">
{% if parent %}
<a href="/?path={{ parent }}" class="block p-3 text-blue-500"><i class="bi bi-arrow-left"></i> 返回</a>
{% endif %}
{% for i in items %}
<div class="flex justify-between items-center p-3 hover:bg-blue-50 dark:hover:bg-slate-700">
<div>
{% if i.is_dir %}
<i class="bi bi-folder"></i> <a href="/?path={{ i.full }}" class="text-blue-500">{{ i.name }}</a>
{% else %}
<i class="bi bi-file-earmark"></i> {{ i.name }}
{% endif %}
<div class="text-xs text-slate-400">{{ i.size }}</div>
</div>
<div class="flex gap-3 text-blue-500">
{% if i.is_dir %}
<a href="/download/{{ i.full }}"><i class="bi bi-download"></i></a>
{% else %}
<a href="/download/{{ i.full }}"><i class="bi bi-download"></i></a>
<a href="javascript:share('{{ i.full }}')"><i class="bi bi-link-45deg"></i></a>
{% endif %}
{% if login %}
<a href="#" data-path="{{ i.full }}" onclick="openDelete(this.dataset.path);return false;"><i class="bi bi-trash"></i></a>
{% endif %}
</div>
</div>
{% endfor %}
</div>

<!-- 登录弹窗 -->
<div id="loginModal" class="fixed inset-0 hidden bg-black/30 backdrop-blur flex items-center justify-center">
<form method="post" action="/login"
 class="bg-white dark:bg-slate-800 p-6 rounded-xl w-72 relative">
<button type="button" onclick="closeLogin()" class="absolute top-2 right-2 text-slate-400"><i class="bi bi-x-lg"></i></button>
<h2 class="text-lg font-bold mb-3">登录</h2>
<!-- 显示登录错误 -->
<div id="loginError" class="text-sm text-red-500 mb-2 hidden"></div>
<input name="username" placeholder="用户名" class="w-full mb-2 p-2 rounded bg-slate-100 dark:bg-slate-700">
<input name="password" type="password" placeholder="密码"
 class="w-full mb-4 p-2 rounded bg-slate-100 dark:bg-slate-700">
<button class="w-full bg-blue-500 text-white py-2 rounded">登录</button>
</form>
</div>

<!-- 新建文件夹 -->
<div id="mkdirModal" class="fixed inset-0 hidden bg-black/30 backdrop-blur flex items-center justify-center">
<div class="bg-white dark:bg-slate-800 p-6 rounded-xl w-72 relative">
<button type="button" onclick="mkdirModal.classList.add('hidden')" class="absolute top-2 right-2 text-slate-400"><i class="bi bi-x-lg"></i></button>
<input id="mkdirName" placeholder="文件夹名"
 class="w-full p-2 rounded bg-slate-100 dark:bg-slate-700">
<button onclick="mkdir()" class="mt-3 w-full bg-blue-500 text-white py-2 rounded">创建</button>
</div>
</div>

<!-- 分享 -->
<div id="shareModal" class="fixed inset-0 hidden bg-black/30 backdrop-blur flex items-center justify-center">
<div class="bg-white dark:bg-slate-800 p-6 rounded-xl w-80 relative">
  <button type="button" onclick="shareModal.classList.add('hidden')" class="absolute top-2 right-2 text-slate-400"><i class="bi bi-x-lg"></i></button>
  <input type="hidden" id="sharePath">
  <div class="mb-2">
    <label class="text-sm text-slate-500 mr-2">有效期:</label>
    <div class="relative inline-block">
      <select id="shareTTL" class="appearance-none pr-8 pl-3 py-2 rounded bg-slate-100 dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-sm cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-300">
        <option value="0">永久</option>
        <option value="3600">1 小时</option>
        <option value="86400">1 天</option>
        <option value="604800">7 天</option>
        <option value="2592000">30 天</option>
      </select>
      <i class="bi bi-chevron-down absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"></i>
    </div>
  </div>

  <div class="mb-2">
    <input id="sharePassword" type="password" placeholder="密码（可选）" class="w-full p-2 rounded bg-slate-100 dark:bg-slate-700">
  </div>

  <div class="mb-2">
    <input id="shareLink" class="w-full p-2 rounded bg-slate-100 dark:bg-slate-700" readonly>
    <div id="shareExpiry" class="text-xs text-slate-400 mt-2"></div>
  </div>

  <div class="flex gap-2">
    <button type="button" onclick="createShare()" class="flex-1 py-2 rounded bg-blue-500 text-white">生成链接</button>
    <button type="button" onclick="copy()" class="flex-1 py-2 rounded border">复制</button>
  </div>
</div>
</div>

<!-- 删除确认 -->
<div id="deleteModal" class="fixed inset-0 hidden bg-black/30 backdrop-blur flex items-center justify-center">
  <div class="bg-white dark:bg-slate-800 p-6 rounded-xl w-80 relative">
    <button type="button" onclick="closeDelete()" class="absolute top-2 right-2 text-slate-400"><i class="bi bi-x-lg"></i></button>
    <h3 class="text-lg font-bold mb-2">确认删除</h3>
    <p class="mb-3 text-slate-500">确认删除 <span id="delName" class="font-medium"></span> 吗？此操作不可恢复。</p>
    <div class="flex gap-2">
      <button onclick="closeDelete()" class="flex-1 py-2 rounded border">取消</button>
      <button onclick="doDelete()" class="flex-1 py-2 rounded bg-red-500 text-white">删除</button>
    </div>
  </div>
</div>

<!-- Toast 容器 -->
<div id="toasts" class="fixed top-4 right-4 space-y-2 z-50"></div>

<script>
let file=null;
dropZone.onclick=()=>fileInput.click();
fileInput.onchange=e=>{
 file=e.target.files[0];
 fileHint.textContent=file.name;
};
dropZone.ondrop=e=>{
 e.preventDefault();
 file=e.dataTransfer.files[0];
 fileHint.textContent=file.name;
}
dropZone.ondragover=e=>e.preventDefault();

function upload(){
 if(!file)return;
 const f=new FormData();f.append("file",file);
 barWrap.classList.remove("hidden");
 const x=new XMLHttpRequest();
 x.open("POST","/upload?path={{ path }}");
 x.upload.onprogress=e=>bar.style.width=(e.loaded/e.total*100)+"%";
 x.onload=()=>location.reload();
 x.send(f);
}

function openLogin(){ 
  const el = document.getElementById('loginError');
  if(el){ el.classList.add('hidden'); el.textContent=''; }
  loginModal.classList.remove("hidden")
}
function closeLogin(){
  const el = document.getElementById('loginError');
  if(el){ el.classList.add('hidden'); el.textContent=''; }
  loginModal.classList.add('hidden')
}
function openMkdir(){mkdirModal.classList.remove("hidden")}
function mkdir(){
 fetch("/mkdir?path={{ path }}&name="+mkdirName.value,{method:"POST"})
 .then(()=>location.reload())
}
function share(p){
  // 打开分享弹窗并填充 path，让用户选择 TTL/密码后点击“生成链接”
  const elPath = document.getElementById('sharePath');
  const elLink = document.getElementById('shareLink');
  const elExpiry = document.getElementById('shareExpiry');
  const elPw = document.getElementById('sharePassword');
  if(elPath) elPath.value = p;
  if(elLink) elLink.value = '';
  if(elExpiry) elExpiry.textContent = '';
  if(elPw) elPw.value = '';
  shareModal.classList.remove("hidden");
}

function createShare(){
  const p = document.getElementById('sharePath').value;
  const ttl = document.getElementById('shareTTL') ? document.getElementById('shareTTL').value : 0;
  const pw = document.getElementById('sharePassword') ? document.getElementById('sharePassword').value : '';
  if(!p) return;
  fetch("/share/"+encodeURIComponent(p)+"?ttl="+encodeURIComponent(ttl)+"&pw="+encodeURIComponent(pw))
    .then(r=>r.json())
    .then(d=>{
      shareLink.value = d.url;
      const expEl = document.getElementById('shareExpiry');
      if(expEl){
        if(d.expires_at){
          const dt = new Date(d.expires_at);
          expEl.textContent = `到期: ${dt.toLocaleString()}`;
        } else {
          expEl.textContent = '永久有效';
        }
      }
    }).catch(()=>showToast('生成分享失败','error'));
}

function formatTTL(sec){
  sec = Number(sec) || 0;
  if(sec<=0) return '永久';
  const days = Math.floor(sec/86400);
  if(days>=1) return `${days} 天`;
  const hours = Math.floor(sec/3600);
  if(hours>=1) return `${hours} 小时`;
  const mins = Math.floor(sec/60);
  if(mins>=1) return `${mins} 分钟`;
  return `${sec} 秒`;
}
function copy(){
  const el = document.getElementById('shareLink');
  if(!el || !el.value){ showToast('请先生成分享链接','error'); return; }
  navigator.clipboard.writeText(el.value).then(()=>showToast('已复制到剪贴板','success')).catch(()=>showToast('复制失败','error'))
}
function toggleDark(){
 document.documentElement.classList.toggle("dark");
 localStorage.theme=document.documentElement.classList.contains("dark")?"dark":"light";
}

let deletePath=null;
function openDelete(p){
  deletePath=p;
  delName.textContent=p;
  deleteModal.classList.remove("hidden");
}
function closeDelete(){
  deletePath=null;
  deleteModal.classList.add("hidden");
}
function doDelete(){
  if(!deletePath) return;
  fetch("/delete/"+encodeURIComponent(deletePath), { headers: { 'Accept': 'application/json' } })
    .then(r=>r.json())
    .then(d=>{
      closeDelete();
      if(d.ok){
        showToast(`删除成功：${d.name}`,'success');
        setTimeout(()=>location.reload(),800);
      }else{
        showToast(`删除失败：${d.error||'未知错误'}`,'error');
      }
    }).catch(()=>showToast('删除失败：网络错误','error'));
}

function showToast(msg, type='info'){
  const container = document.getElementById('toasts');
  if(!container) return;
  const el = document.createElement('div');
  const color = type==='success' ? 'bg-green-500' : type==='error' ? 'bg-red-500' : 'bg-blue-500';
  el.className = `text-white px-4 py-2 rounded shadow ${color} opacity-0 transform translate-y-2 transition-all duration-300`;
  el.textContent = msg;
  container.appendChild(el);
  requestAnimationFrame(()=>{ el.classList.remove('opacity-0'); el.classList.remove('translate-y-2'); el.classList.add('opacity-100'); });
  setTimeout(()=>{
    el.classList.add('opacity-0');
    el.addEventListener('transitionend', ()=>el.remove(), {once:true});
  }, 3000);
}

// 页面加载时检查 URL 参数显示提示
(function(){
  const params = new URLSearchParams(location.search);
  const m = params.get('msg');
  if(m){
    if(m==='login_failed'){
      openLogin();
      const el = document.getElementById('loginError');
      if(el){ el.textContent='登录失败：用户名或密码错误'; el.classList.remove('hidden'); }
    }
    else if(m==='deleted'){
      const name = params.get('name');
      showToast(name ? `删除成功：${name}` : '删除成功','success');
    }else if(m==='delete_failed'){
      const name = params.get('name');
      showToast(name ? `删除失败：${name}` : '删除失败','error');
    }
    history.replaceState(null, '', location.pathname);
  }
})();
</script>
</div>
</body>
</html>
"""

def render(**ctx):
    return HTMLResponse(Template(HTML).render(**ctx))

# ================== 路由 ==================
@app.get("/")
async def index(request: Request, path: str = ""):
    p = safe_path(path)
    # 不再对未登录用户自动创建目录
    if not p.exists():
        if not is_login(request):
            return HTMLResponse("目录不存在", status_code=404)
        p.mkdir(parents=True, exist_ok=True)
    items=[]
    for f in sorted(p.iterdir()):
        items.append({
            "name": f.name,
            "full": f"{path}/{f.name}".strip("/"),
            "is_dir": f.is_dir(),
            "size": human(dir_size(f))
        })
    parent="/".join(path.split("/")[:-1])
    return render(
        items=items,
        path=path,
        parent=parent,
        total_size=human(dir_size(p)),
        login=is_login(request)
    )

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if username==USERNAME and password==PASSWORD:
        t=secrets.token_urlsafe(16)
        TOKENS[t]=time.time()+TOKEN_TTL
        r=RedirectResponse("/",302)
        r.set_cookie("token",t,httponly=True)
        return r
    return RedirectResponse("/?msg=login_failed",302)
    
@app.get("/logout")
async def logout():
    r=RedirectResponse("/",302)
    r.delete_cookie("token")
    return r

@app.post("/upload")
async def upload(request: Request, path: str="", file: UploadFile=File(...)):
    if not is_login(request): return RedirectResponse("/",302)
    d=safe_path(path); d.mkdir(exist_ok=True)
    with open(d/file.filename,"wb") as f:
        f.write(await file.read())
    return "ok"

@app.post("/mkdir")
async def mkdir(request: Request, path: str="", name: str=""):
    if not is_login(request): return ""
    (safe_path(path)/name).mkdir(exist_ok=True)
    return "ok"

@app.get("/delete/{path:path}")
async def delete(request: Request, path: str):
    if not is_login(request):
        if 'application/json' in request.headers.get('accept',''):
            return JSONResponse({"ok": False, "error": "未登录"})
        return RedirectResponse("/?msg=delete_failed",302)

    p = safe_path(path)
    try:
        if p.is_file():
            p.unlink()
        else:
            for f in p.rglob("*"):
                if f.is_file(): f.unlink()
            p.rmdir()
    except Exception as e:
        if 'application/json' in request.headers.get('accept',''):
            return JSONResponse({"ok": False, "error": str(e)})
        return RedirectResponse(f"/?msg=delete_failed&name={quote(p.name)}",302)

    if 'application/json' in request.headers.get('accept',''):
        return JSONResponse({"ok": True, "name": p.name})
    return RedirectResponse(f"/?msg=deleted&name={quote(p.name)}",302)

@app.get("/download/{path:path}")
async def download(path: str):
    p=safe_path(path)
    if p.is_dir():
        buf=zip_dir(p)
        return StreamingResponse(buf, media_type="application/zip",
            headers={"Content-Disposition":f"attachment; filename={p.name}.zip"})
    return FileResponse(p, filename=p.name)

@app.get("/share/{path:path}")
async def share(path: str, ttl: int = 0, pw: str = ""):
    t=secrets.token_urlsafe(8)
    exp = None
    if ttl and ttl > 0:
        exp = time.time() + int(ttl)
    entry = {"path":path,"exp":exp}
    if pw:
        entry["pw_hash"] = hash_pw(pw)
    SHARES[t]=entry
    save_shares()
    expires_at = datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else None
    return JSONResponse({"url": f"{BASE_URL}/s/{t}", "expires_in": ttl if ttl and int(ttl) > 0 else None, "expires_at": expires_at})

@app.get("/s/{token}")
async def shared(token: str):
    d=SHARES.get(token)
    if not d: return HTMLResponse("链接已失效",404)
    if d.get("exp") and d["exp"]<time.time(): return HTMLResponse("链接已失效",404)
    p=safe_path(d["path"])
    if d.get("exp"):
        exp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["exp"]))
    else:
        exp_str = "永久有效"
    parent = "/".join(d["path"].split("/")[:-1])
    parent_link = f"{BASE_URL}/?path={quote(parent)}" if parent else f"{BASE_URL}/"
    return HTMLResponse(f"""<!doctype html>
<html class="h-full">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
<title>下载</title>
</head>
<body class="h-full bg-slate-100 flex items-center justify-center">
<div class="bg-white p-8 rounded-xl text-center">
<i class="bi bi-file-earmark text-4xl text-blue-500 block mb-3"></i>
<h1 class="text-2xl font-bold mb-2">{p.name}</h1>
<p class="text-slate-400 mb-2">有效期: {exp_str}</p>
<p class="text-slate-500 mb-2">路径: <a href="{parent_link}" class="text-blue-500 hover:underline">/{parent if parent else ''}</a></p>
<p class="text-slate-500 mb-6">文件大小: {human(dir_size(p))}</p>
<a href="/download-share/{token}" class="inline-block px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"><i class="bi bi-download"></i> 下载</a>
</div>
</body>
</html>
""")

@app.get("/download-share/{token}")
async def download_share_get(token: str):
    d=SHARES.get(token)
    if not d: return HTMLResponse("链接已失效",404)
    if d.get("exp") and d["exp"]<time.time(): return HTMLResponse("链接已失效",404)
    if d.get("pw_hash"):
        # 返回一个简单的密码表单（POST 到同一路径）
        return HTMLResponse(f"""<!doctype html>
<html>
<body>
  <form method="post" action="/download-share/{token}">
    <p>请输入密码：</p>
    <input name="pw" type="password">
    <button type="submit">下载</button>
  </form>
</body>
</html>""")
    return await download(d["path"])

@app.post("/download-share/{token}")
async def download_share_post(token: str, pw: str = Form(None)):
    d=SHARES.get(token)
    if not d: return HTMLResponse("链接已失效",404)
    if d.get("exp") and d["exp"]<time.time(): return HTMLResponse("链接已失效",404)
    if d.get("pw_hash"):
        if not pw or hash_pw(pw) != d.get("pw_hash"):
            return HTMLResponse("密码错误", status_code=403)
    return await download(d["path"])
