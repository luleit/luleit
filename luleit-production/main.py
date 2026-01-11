from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import fitz  # PyMuPDF
import os, json, base64, uuid, io, math, secrets
from datetime import datetime
from typing import List
from collections import defaultdict

try:
    from PIL import Image
except:
    Image = None

# ============ CONFIG ============
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "luleit2024")

app = FastAPI(title="Luleit PDF Editor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# ============ ANALYTICS STORAGE ============
user_counter = {"count": 0}  # Sequential user IDs

analytics = {
    "total_uploads": 0,
    "total_downloads": 0,
    "total_merges": 0,
    "total_splits": 0,
    "total_compressions": 0,
    "total_conversions": 0,
    "total_protects": 0,
    "total_unlocks": 0,
    "total_watermarks": 0,
    "total_page_numbers": 0,
    "total_rotations": 0,
    "total_edits": 0,
    "browsers": defaultdict(int),
    "countries": defaultdict(int),
    "referrers": defaultdict(int),
    "feature_usage": defaultdict(int),
    "recent_activities": [],
    "daily_stats": defaultdict(lambda: {"uploads": 0, "downloads": 0, "users": set()}),
}

# User sessions with sequential IDs and time tracking
user_sessions = {}  # ip -> session data

pdf_storage = {}

# ============ HELPERS ============
def get_or_create_user(request: Request):
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "Unknown")
    country = request.headers.get("cf-ipcountry", request.headers.get("x-vercel-ip-country", "Unknown"))
    referer = request.headers.get("referer", "Direct")
    
    # Parse browser
    browser = "Other"
    if "Chrome" in ua and "Edg" not in ua: browser = "Chrome"
    elif "Firefox" in ua: browser = "Firefox"
    elif "Safari" in ua and "Chrome" not in ua: browser = "Safari"
    elif "Edg" in ua: browser = "Edge"
    
    now = datetime.now()
    
    if ip not in user_sessions:
        user_counter["count"] += 1
        user_sessions[ip] = {
            "user_id": user_counter["count"],
            "ip": ip,
            "browser": browser,
            "country": country,
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "session_start": now.isoformat(),
            "total_time_seconds": 0,
            "actions": [],
            "action_count": 0,
            "documents_edited": 0,
            "pages_processed": 0
        }
        analytics["browsers"][browser] += 1
        analytics["countries"][country] += 1
        if referer != "Direct" and "://" in referer:
            try:
                domain = referer.split("/")[2]
                analytics["referrers"][domain] += 1
            except: pass
    else:
        # Update session time
        last_seen = datetime.fromisoformat(user_sessions[ip]["last_seen"])
        time_diff = (now - last_seen).total_seconds()
        # Only count if less than 30 min gap (active session)
        if time_diff < 1800:
            user_sessions[ip]["total_time_seconds"] += time_diff
        else:
            # New session
            user_sessions[ip]["session_start"] = now.isoformat()
    
    user_sessions[ip]["last_seen"] = now.isoformat()
    return user_sessions[ip]

def track_action(request: Request, action: str, details: str = ""):
    user = get_or_create_user(request)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    # Update user
    user["action_count"] += 1
    user["actions"].append({
        "action": action,
        "details": details,
        "time": now.isoformat()
    })
    # Keep last 20 actions per user
    user["actions"] = user["actions"][-20:]
    
    # Update daily stats
    analytics["daily_stats"][today]["users"].add(user["user_id"])
    if action == "Upload":
        analytics["daily_stats"][today]["uploads"] += 1
    elif action == "Download":
        analytics["daily_stats"][today]["downloads"] += 1
    
    # Add to recent activities
    activity = {
        "user_id": user["user_id"],
        "action": action,
        "details": details,
        "time": now.isoformat(),
        "country": user["country"],
        "browser": user["browser"]
    }
    analytics["recent_activities"].insert(0, activity)
    analytics["recent_activities"] = analytics["recent_activities"][:200]
    
    # Track feature
    analytics["feature_usage"][action.lower()] += 1

def format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not (secrets.compare_digest(credentials.username, ADMIN_USERNAME) and 
            secrets.compare_digest(credentials.password, ADMIN_PASSWORD)):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return True

def extract_font_family(font_name):
    if not font_name: return "Arial, sans-serif"
    fl = font_name.lower()
    if "arial" in fl or "helvetica" in fl: return "Arial, sans-serif"
    elif "times" in fl: return "Times New Roman, serif"
    elif "courier" in fl: return "Courier New, monospace"
    return "Arial, sans-serif"

def generate_preview(content, max_pages=50):
    doc = fitz.open(stream=content, filetype="pdf")
    pages = []
    for i in range(min(len(doc), max_pages)):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        pages.append({"pageNum": i+1, "width": pix.width, "height": pix.height, 
                      "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()})
    doc.close()
    return pages

# ============ ROUTES ============
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    get_or_create_user(request)  # Track visit
    with open("index.html") as f:
        return f.read()

@app.get("/admin", response_class=HTMLResponse)
async def admin_portal(auth: bool = Depends(verify_admin)):
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Luleit Admin Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        :root{--bg:#0f172a;--bg2:#1e293b;--bg3:#334155;--text:#fff;--text2:#94a3b8;--accent:#6366f1;--success:#10b981;--warn:#f59e0b;--danger:#ef4444}
        body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
        .header{background:var(--bg2);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bg3)}
        .logo{font-size:20px;font-weight:700;display:flex;align-items:center;gap:10px}
        .logo i{color:var(--accent)}
        .header-right{display:flex;align-items:center;gap:12px}
        .live{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--success)}
        .live-dot{width:8px;height:8px;background:var(--success);border-radius:50%;animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
        .btn{padding:8px 16px;border:none;border-radius:6px;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px;background:var(--bg3);color:var(--text2)}
        .btn:hover{background:var(--accent);color:#fff}
        .main{padding:24px;max-width:1400px;margin:0 auto}
        .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px}
        .stat{background:var(--bg2);border-radius:12px;padding:20px}
        .stat-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;margin-bottom:12px}
        .stat-icon.purple{background:rgba(99,102,241,0.2);color:var(--accent)}
        .stat-icon.green{background:rgba(16,185,129,0.2);color:var(--success)}
        .stat-icon.orange{background:rgba(245,158,11,0.2);color:var(--warn)}
        .stat-icon.pink{background:rgba(236,72,153,0.2);color:#ec4899}
        .stat-value{font-size:28px;font-weight:700}
        .stat-label{font-size:12px;color:var(--text2);margin-top:4px}
        .grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px}
        @media(max-width:900px){.grid{grid-template-columns:1fr}}
        .card{background:var(--bg2);border-radius:12px;padding:20px}
        .card-title{font-size:14px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px;color:var(--text2)}
        .card-title i{color:var(--accent)}
        .chart-box{height:250px}
        .activity{max-height:400px;overflow-y:auto}
        .activity-item{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--bg3)}
        .activity-item:last-child{border-bottom:none}
        .activity-badge{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}
        .activity-badge.upload{background:rgba(99,102,241,0.2);color:var(--accent)}
        .activity-badge.download{background:rgba(16,185,129,0.2);color:var(--success)}
        .activity-badge.merge{background:rgba(245,158,11,0.2);color:var(--warn)}
        .activity-badge.compress{background:rgba(236,72,153,0.2);color:#ec4899}
        .activity-badge.edit{background:rgba(139,92,246,0.2);color:#8b5cf6}
        .activity-badge.convert{background:rgba(20,184,166,0.2);color:#14b8a6}
        .activity-info{flex:1;min-width:0}
        .activity-action{font-size:13px;font-weight:500}
        .activity-meta{font-size:11px;color:var(--text2);margin-top:2px}
        .activity-time{font-size:11px;color:var(--text2);flex-shrink:0}
        .user-id{background:var(--accent);color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
        table{width:100%;border-collapse:collapse}
        th,td{padding:12px;text-align:left;border-bottom:1px solid var(--bg3);font-size:13px}
        th{color:var(--text2);font-weight:500;font-size:11px;text-transform:uppercase}
        tr:hover{background:rgba(255,255,255,0.02)}
        .empty{text-align:center;padding:40px;color:var(--text2)}
        .progress{height:6px;background:var(--bg3);border-radius:3px;margin-top:8px;overflow:hidden}
        .progress-bar{height:100%;background:var(--accent);border-radius:3px}
        .feature-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3)}
        .feature-row:last-child{border-bottom:none}
        .feature-name{font-size:13px;display:flex;align-items:center;gap:8px}
        .feature-count{font-weight:600}
        .flag{font-size:18px}
        .tabs{display:flex;gap:8px;margin-bottom:16px}
        .tab{padding:8px 16px;background:var(--bg3);border:none;border-radius:6px;color:var(--text2);cursor:pointer;font-size:12px}
        .tab.active{background:var(--accent);color:#fff}
    </style>
</head>
<body>
    <header class="header">
        <div class="logo"><i class="fas fa-chart-pie"></i> Luleit Admin</div>
        <div class="header-right">
            <div class="live"><div class="live-dot"></div> Live</div>
            <button class="btn" onclick="loadData()"><i class="fas fa-sync"></i> Refresh</button>
            <button class="btn" onclick="location.href='/'"><i class="fas fa-external-link-alt"></i> View Site</button>
        </div>
    </header>
    <main class="main">
        <div class="stats" id="stats"></div>
        <div class="grid">
            <div class="card">
                <div class="card-title"><i class="fas fa-chart-line"></i> Usage (Last 7 Days)</div>
                <div class="chart-box"><canvas id="chart"></canvas></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-bolt"></i> Recent Activity</div>
                <div class="activity" id="activity"></div>
            </div>
        </div>
        <div class="grid">
            <div class="card">
                <div class="card-title"><i class="fas fa-users"></i> Users (Ordered by ID)</div>
                <div style="max-height:400px;overflow-y:auto">
                    <table><thead><tr><th>User</th><th>Country</th><th>Browser</th><th>Time on Site</th><th>Actions</th><th>Last Seen</th></tr></thead><tbody id="users"></tbody></table>
                </div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-fire"></i> Feature Usage</div>
                <div id="features"></div>
            </div>
        </div>
        <div class="grid">
            <div class="card">
                <div class="card-title"><i class="fas fa-globe"></i> Top Countries</div>
                <div id="countries"></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-desktop"></i> Browsers</div>
                <div class="chart-box" style="height:180px"><canvas id="browserChart"></canvas></div>
            </div>
        </div>
    </main>
<script>
let chart = null;
let browserChart = null;

async function loadData() {
    const res = await fetch('/admin/api/stats');
    const d = await res.json();
    
    // Stats
    document.getElementById('stats').innerHTML = `
        <div class="stat"><div class="stat-icon purple"><i class="fas fa-users"></i></div><div class="stat-value">${d.total_users}</div><div class="stat-label">Total Users</div></div>
        <div class="stat"><div class="stat-icon green"><i class="fas fa-upload"></i></div><div class="stat-value">${d.total_uploads}</div><div class="stat-label">Total Uploads</div></div>
        <div class="stat"><div class="stat-icon orange"><i class="fas fa-download"></i></div><div class="stat-value">${d.total_downloads}</div><div class="stat-label">Total Downloads</div></div>
        <div class="stat"><div class="stat-icon pink"><i class="fas fa-clock"></i></div><div class="stat-value">${d.today_users}</div><div class="stat-label">Users Today</div></div>
    `;
    
    // Activity
    const actHtml = d.recent_activities.slice(0,15).map(a => `
        <div class="activity-item">
            <div class="activity-badge ${a.action.toLowerCase()}"><i class="fas fa-${getIcon(a.action)}"></i></div>
            <div class="activity-info">
                <div class="activity-action"><span class="user-id">User #${a.user_id}</span> ${a.action}</div>
                <div class="activity-meta">${a.details || 'No details'} • ${a.country} • ${a.browser}</div>
            </div>
            <div class="activity-time">${timeAgo(a.time)}</div>
        </div>
    `).join('') || '<div class="empty">No activity yet. Visit the main site to generate data!</div>';
    document.getElementById('activity').innerHTML = actHtml;
    
    // Users table (sorted by user_id)
    const users = d.users.sort((a,b) => a.user_id - b.user_id);
    const usersHtml = users.map(u => `
        <tr>
            <td><span class="user-id">User #${u.user_id}</span></td>
            <td>${getFlag(u.country)} ${u.country}</td>
            <td>${u.browser}</td>
            <td><strong>${u.time_on_site}</strong></td>
            <td>${u.action_count}</td>
            <td>${timeAgo(u.last_seen)}</td>
        </tr>
    `).join('') || '<tr><td colspan="6" class="empty">No users yet</td></tr>';
    document.getElementById('users').innerHTML = usersHtml;
    
    // Features
    const features = Object.entries(d.feature_usage).sort((a,b) => b[1] - a[1]);
    const maxF = Math.max(...features.map(f => f[1]), 1);
    const featHtml = features.map(([n,c]) => `
        <div class="feature-row"><div class="feature-name"><i class="fas fa-${getIcon(n)}"></i> ${n}</div><div class="feature-count">${c}</div></div>
        <div class="progress"><div class="progress-bar" style="width:${(c/maxF)*100}%"></div></div>
    `).join('') || '<div class="empty">No features used yet</div>';
    document.getElementById('features').innerHTML = featHtml;
    
    // Countries
    const countries = Object.entries(d.countries).sort((a,b) => b[1] - a[1]).slice(0,8);
    const countriesHtml = countries.map(([c,n]) => `
        <div class="feature-row"><div class="feature-name">${getFlag(c)} ${c}</div><div class="feature-count">${n}</div></div>
    `).join('') || '<div class="empty">No country data yet</div>';
    document.getElementById('countries').innerHTML = countriesHtml;
    
    // Usage chart
    const daily = d.daily_stats;
    const labels = Object.keys(daily).slice(-7);
    const uploads = labels.map(l => daily[l]?.uploads || 0);
    const downloads = labels.map(l => daily[l]?.downloads || 0);
    
    if (chart) chart.destroy();
    chart = new Chart(document.getElementById('chart'), {
        type: 'line',
        data: {
            labels: labels.map(l => l.slice(5)),
            datasets: [
                {label: 'Uploads', data: uploads, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.1)', fill: true, tension: 0.4},
                {label: 'Downloads', data: downloads, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', fill: true, tension: 0.4}
            ]
        },
        options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {labels: {color: '#94a3b8'}}}, scales: {x: {grid: {color: '#334155'}, ticks: {color: '#94a3b8'}}, y: {grid: {color: '#334155'}, ticks: {color: '#94a3b8'}}}}
    });
    
    // Browser chart
    const browsers = Object.entries(d.browsers);
    if (browserChart) browserChart.destroy();
    browserChart = new Chart(document.getElementById('browserChart'), {
        type: 'doughnut',
        data: {
            labels: browsers.map(b => b[0]),
            datasets: [{data: browsers.map(b => b[1]), backgroundColor: ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']}]
        },
        options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {position: 'right', labels: {color: '#94a3b8'}}}}
    });
}

function getIcon(a) {
    const icons = {upload:'upload',download:'download',merge:'layer-group',compress:'compress',split:'cut',convert:'exchange-alt',protect:'lock',edit:'edit',watermark:'tint',rotate:'sync','page-numbers':'list-ol',unlock:'unlock'};
    return icons[a.toLowerCase()] || 'circle';
}

function getFlag(c) {
    if (!c || c === 'Unknown') return '🌍';
    try { return String.fromCodePoint(...[...c.toUpperCase()].map(x => 127397 + x.charCodeAt(0))); } catch { return '🌍'; }
}

function timeAgo(t) {
    const diff = Math.floor((new Date() - new Date(t)) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff/60) + 'm ago';
    if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
    return Math.floor(diff/86400) + 'd ago';
}

loadData();
setInterval(loadData, 10000);
</script>
</body>
</html>"""

@app.get("/admin/api/stats")
async def admin_stats(auth: bool = Depends(verify_admin)):
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Format daily stats
    daily = {}
    for date, stats in analytics["daily_stats"].items():
        daily[date] = {"uploads": stats["uploads"], "downloads": stats["downloads"], "users": len(stats["users"])}
    
    # Format users with time on site
    users = []
    for ip, u in user_sessions.items():
        users.append({
            "user_id": u["user_id"],
            "country": u["country"],
            "browser": u["browser"],
            "time_on_site": format_duration(u["total_time_seconds"]),
            "action_count": u["action_count"],
            "first_seen": u["first_seen"],
            "last_seen": u["last_seen"],
            "documents_edited": u["documents_edited"],
            "pages_processed": u["pages_processed"]
        })
    
    return {
        "total_users": len(user_sessions),
        "total_uploads": analytics["total_uploads"],
        "total_downloads": analytics["total_downloads"],
        "today_users": len(analytics["daily_stats"][today]["users"]) if today in analytics["daily_stats"] else 0,
        "today_uploads": analytics["daily_stats"][today]["uploads"] if today in analytics["daily_stats"] else 0,
        "daily_stats": daily,
        "browsers": dict(analytics["browsers"]),
        "countries": dict(analytics["countries"]),
        "referrers": dict(analytics["referrers"]),
        "feature_usage": dict(analytics["feature_usage"]),
        "recent_activities": analytics["recent_activities"][:50],
        "users": users
    }

# ============ PDF OPERATIONS ============
@app.post("/upload-pdf")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 50*1024*1024:
        raise HTTPException(400, "File too large (max 50MB)")
    
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except:
        raise HTTPException(400, "Invalid PDF file")
    
    # Track
    analytics["total_uploads"] += 1
    user = get_or_create_user(request)
    user["documents_edited"] += 1
    user["pages_processed"] += len(doc)
    track_action(request, "Upload", f"{file.filename} ({len(doc)} pages)")
    
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content": content, "filename": file.filename, "created": datetime.now()}
    
    # Extract text and build response
    fonts_used, sizes_used, colors_used = {}, {}, {}
    pages_data = []
    
    for pn in range(len(doc)):
        page = doc[pn]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
        
        text_blocks = []
        try:
            for block in page.get_text("dict")["blocks"]:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            if span["text"].strip():
                                fn = span.get("font", "Arial")
                                fs = round(span.get("size", 12), 1)
                                ci = span.get("color", 0)
                                ch = "#{:06x}".format(ci) if isinstance(ci, int) else "#000000"
                                fonts_used[fn] = fonts_used.get(fn, 0) + 1
                                sizes_used[fs] = sizes_used.get(fs, 0) + 1
                                colors_used[ch] = colors_used.get(ch, 0) + 1
                                text_blocks.append({
                                    "text": span["text"],
                                    "x": span["bbox"][0] * 2.5, "y": span["bbox"][1] * 2.5,
                                    "width": (span["bbox"][2] - span["bbox"][0]) * 2.5,
                                    "height": (span["bbox"][3] - span["bbox"][1]) * 2.5,
                                    "fontSize": span["size"] * 2.5,
                                    "fontFamily": extract_font_family(fn),
                                    "color": ch,
                                    "originalX": span["bbox"][0], "originalY": span["bbox"][1],
                                    "originalWidth": span["bbox"][2] - span["bbox"][0],
                                    "originalHeight": span["bbox"][3] - span["bbox"][1],
                                    "originalFontSize": span["size"]
                                })
        except: pass
        
        pages_data.append({
            "pageNum": pn + 1,
            "width": pix.width, "height": pix.height,
            "originalWidth": page.rect.width, "originalHeight": page.rect.height,
            "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode(),
            "textBlocks": text_blocks
        })
    
    doc.close()
    fs = len(content)
    
    return {
        "pdfId": pdf_id,
        "pageCount": len(pages_data),
        "pages": pages_data,
        "fileName": file.filename,
        "fileSize": f"{fs/1024:.1f} KB" if fs < 1024*1024 else f"{fs/1024/1024:.1f} MB",
        "documentStyle": {
            "fontFamilies": list(set(extract_font_family(f) for f in fonts_used)) or ["Arial, sans-serif"],
            "sizes": sorted(sizes_used.keys()) or [12],
            "colors": list(colors_used.keys())[:10] or ["#000000"],
            "defaultSize": max(sizes_used, key=sizes_used.get) if sizes_used else 12,
            "defaultColor": max(colors_used, key=colors_used.get) if colors_used else "#000000"
        }
    }

@app.post("/merge-pdfs")
async def merge_pdfs(request: Request, files: List[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(400, "Need at least 2 files")
    
    merged = fitz.open()
    for f in files:
        content = await f.read()
        pdf = fitz.open(stream=content, filetype="pdf")
        merged.insert_pdf(pdf)
        pdf.close()
    
    analytics["total_merges"] += 1
    track_action(request, "Merge", f"{len(files)} files combined")
    
    pdf_id = str(uuid.uuid4())
    output = io.BytesIO()
    merged.save(output)
    merged.close()
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content": content, "filename": "merged.pdf", "created": datetime.now()}
    
    return {
        "pdfId": pdf_id,
        "pageCount": fitz.open(stream=content, filetype="pdf").page_count,
        "pages": generate_preview(content),
        "fileName": "merged.pdf",
        "fileSize": f"{len(content)/1024:.1f} KB"
    }

@app.post("/extract-pages")
async def extract_pages(request: Request, pdf_id: str = Form(...), pages: str = Form(...)):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page_list = json.loads(pages)
    
    new_doc = fitz.open()
    for p in page_list:
        if 1 <= p <= len(doc):
            new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)
    
    analytics["total_splits"] += 1
    track_action(request, "Split", f"{len(page_list)} pages extracted")
    
    output = io.BytesIO()
    new_doc.save(output)
    new_doc.close()
    doc.close()
    
    new_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[new_id] = {"content": content, "filename": "extracted.pdf", "created": datetime.now()}
    
    return {
        "pdfId": new_id,
        "pageCount": len(page_list),
        "pages": generate_preview(content),
        "fileName": "extracted.pdf",
        "fileSize": f"{len(content)/1024:.1f} KB"
    }

@app.post("/compress-pdf")
async def compress_pdf(request: Request, file: UploadFile = File(...), quality: str = Form("medium")):
    content = await file.read()
    original_size = len(content)
    doc = fitz.open(stream=content, filetype="pdf")
    
    if Image:
        q = {"high": 90, "medium": 70, "low": 50}.get(quality, 70)
        for page in doc:
            for img in page.get_images():
                try:
                    xref = img[0]
                    base = doc.extract_image(xref)
                    pil_img = Image.open(io.BytesIO(base["image"]))
                    if pil_img.mode in ('RGBA', 'P'):
                        pil_img = pil_img.convert('RGB')
                    out = io.BytesIO()
                    pil_img.save(out, format='JPEG', quality=q, optimize=True)
                    page.replace_image(xref, stream=out.getvalue())
                except: pass
    
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True, clean=True)
    doc.close()
    compressed = output.getvalue()
    
    analytics["total_compressions"] += 1
    savings = ((original_size - len(compressed)) / original_size) * 100
    track_action(request, "Compress", f"Saved {savings:.1f}%")
    
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content": compressed, "filename": f"compressed_{file.filename}", "created": datetime.now()}
    
    return {
        "pdfId": pdf_id,
        "originalSize": f"{original_size/1024:.1f} KB",
        "compressedSize": f"{len(compressed)/1024:.1f} KB",
        "savings": f"{savings:.1f}%",
        "fileName": f"compressed_{file.filename}"
    }

@app.post("/page-operations")
async def page_operations(request: Request, pdf_id: str = Form(...), operations: str = Form(...)):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    ops = json.loads(operations)
    
    for op in ops:
        if op["type"] == "rotate" and 0 <= op["page"]-1 < len(doc):
            doc[op["page"]-1].set_rotation(doc[op["page"]-1].rotation + op["angle"])
    
    for p in sorted([op["page"]-1 for op in ops if op["type"] == "delete"], reverse=True):
        if 0 <= p < len(doc) and len(doc) > 1:
            doc.delete_page(p)
    
    track_action(request, "Edit", "Page operations")
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success": True, "pageCount": fitz.open(stream=content, filetype="pdf").page_count, "pages": generate_preview(content)}

@app.post("/add-watermark")
async def add_watermark(request: Request, pdf_id: str = Form(...), text: str = Form(""), position: str = Form("center"), rotation: float = Form(-45)):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    for page in doc:
        rect = page.rect
        fontsize = min(rect.width, rect.height) / 8
        tw = fitz.get_text_length(text, fontsize=fontsize)
        x, y = rect.width/2 - tw/2, rect.height/2
        page.insert_text(fitz.Point(x, y), text, fontsize=fontsize, color=(0.7, 0.7, 0.7), rotate=rotation)
    
    analytics["total_watermarks"] += 1
    track_action(request, "Watermark", text)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success": True, "pages": generate_preview(content)}

@app.post("/add-page-numbers")
async def add_page_numbers(request: Request, pdf_id: str = Form(...), position: str = Form("bottom-center"), start_num: int = Form(1), format_str: str = Form("Page {n} of {total}")):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    total = len(doc)
    
    for i, page in enumerate(doc):
        rect = page.rect
        text = format_str.replace("{n}", str(start_num + i)).replace("{total}", str(total))
        tw = fitz.get_text_length(text, fontsize=10)
        y = rect.height - 30 if "bottom" in position else 40
        x = (rect.width - tw) / 2
        page.insert_text(fitz.Point(x, y), text, fontsize=10, color=(0, 0, 0))
    
    analytics["total_page_numbers"] += 1
    track_action(request, "Page-Numbers", format_str)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success": True, "pages": generate_preview(content)}

@app.post("/protect-pdf")
async def protect_pdf(request: Request, pdf_id: str = Form(...), password: str = Form(...), permissions: str = Form("all")):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    perm = fitz.PDF_PERM_ACCESSIBILITY
    if permissions == "all":
        perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_MODIFY
    
    output = io.BytesIO()
    doc.save(output, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, permissions=perm)
    doc.close()
    
    analytics["total_protects"] += 1
    track_action(request, "Protect", "Password added")
    
    new_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[new_id] = {"content": content, "filename": "protected.pdf", "created": datetime.now()}
    
    return {"pdfId": new_id, "fileName": "protected.pdf", "fileSize": f"{len(content)/1024:.1f} KB"}

@app.post("/unlock-pdf")
async def unlock_pdf(request: Request, file: UploadFile = File(...), password: str = Form(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    if doc.is_encrypted and not doc.authenticate(password):
        raise HTTPException(400, "Wrong password")
    
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    
    analytics["total_unlocks"] += 1
    track_action(request, "Unlock", "Password removed")
    
    new_id = str(uuid.uuid4())
    unlocked = output.getvalue()
    pdf_storage[new_id] = {"content": unlocked, "filename": "unlocked.pdf", "created": datetime.now()}
    
    return {"pdfId": new_id, "pages": generate_preview(unlocked), "fileName": "unlocked.pdf"}

@app.post("/pdf-to-images")
async def pdf_to_images(request: Request, file: UploadFile = File(...), format: str = Form("png")):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    images = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        if format == "jpg" and Image:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=90)
            img_bytes = out.getvalue()
            mime = "image/jpeg"
        else:
            img_bytes = pix.tobytes("png")
            mime = "image/png"
        images.append({"page": i+1, "data": f"data:{mime};base64," + base64.b64encode(img_bytes).decode(), "filename": f"page_{i+1}.{format}"})
    
    doc.close()
    analytics["total_conversions"] += 1
    track_action(request, "Convert", f"PDF to {format.upper()}")
    
    return {"images": images}

@app.post("/images-to-pdf")
async def images_to_pdf(request: Request, files: List[UploadFile] = File(...)):
    doc = fitz.open()
    
    for f in files:
        img_bytes = await f.read()
        if Image:
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            w, h = img.size
            page = doc.new_page(width=w, height=h)
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=90)
            page.insert_image(fitz.Rect(0, 0, w, h), stream=out.getvalue())
        else:
            page = doc.new_page(width=595, height=842)
            page.insert_image(fitz.Rect(50, 50, 545, 792), stream=img_bytes)
    
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    
    analytics["total_conversions"] += 1
    track_action(request, "Convert", f"{len(files)} images to PDF")
    
    pdf_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content": content, "filename": "images.pdf", "created": datetime.now()}
    
    return {"pdfId": pdf_id, "pageCount": len(files), "pages": generate_preview(content), "fileName": "images.pdf", "fileSize": f"{len(content)/1024:.1f} KB"}

@app.post("/rotate-all")
async def rotate_all(request: Request, pdf_id: str = Form(...), angle: int = Form(90)):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    for page in doc:
        page.set_rotation(page.rotation + angle)
    
    analytics["total_rotations"] += 1
    track_action(request, "Rotate", f"All pages {angle}°")
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success": True, "pages": generate_preview(content)}

# ============ FREE DOWNLOADS ============
@app.get("/download/{pdf_id}")
async def download_pdf(request: Request, pdf_id: str):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    s = pdf_storage[pdf_id]
    analytics["total_downloads"] += 1
    track_action(request, "Download", s["filename"])
    
    return StreamingResponse(io.BytesIO(s["content"]), media_type="application/pdf", 
                            headers={"Content-Disposition": f"attachment; filename={s['filename']}"})

@app.get("/download-direct/{pdf_id}")
async def download_direct(request: Request, pdf_id: str):
    return await download_pdf(request, pdf_id)

@app.post("/save-and-download")
async def save_and_download(request: Request, pdf_id: str = Form(...), edits: str = Form("[]")):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    content = pdf_storage[pdf_id]["content"]
    edit_list = json.loads(edits) if edits else []
    
    if edit_list:
        doc = fitz.open(stream=content, filetype="pdf")
        for e in edit_list:
            try:
                page = doc[e["pageNum"] - 1]
                rect = fitz.Rect(e["originalX"] - 1, e["originalY"] - 1, 
                                e["originalX"] + e["originalWidth"] + 5, 
                                e["originalY"] + e["originalHeight"] + 1)
                page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
                if e.get("newText"):
                    page.insert_text(fitz.Point(e["originalX"], e["originalY"] + e["originalHeight"] - 2), 
                                    e["newText"], fontsize=e["originalFontSize"])
            except: pass
        
        output = io.BytesIO()
        doc.save(output)
        doc.close()
        content = output.getvalue()
        analytics["total_edits"] += len(edit_list)
    
    analytics["total_downloads"] += 1
    track_action(request, "Download", f"With {len(edit_list)} edits")
    
    return StreamingResponse(io.BytesIO(content), media_type="application/pdf",
                            headers={"Content-Disposition": "attachment; filename=luleit-edited.pdf"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
