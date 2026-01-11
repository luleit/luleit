from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import fitz  # PyMuPDF
import os, json, tempfile, base64, uuid, io, math, re, secrets
from datetime import datetime, timedelta
from typing import List, Optional
from collections import defaultdict

try:
    from PIL import Image
except:
    Image = None

# ============ CONFIG ============
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "luleit2024")  # Change this!
DOMAIN = os.getenv("DOMAIN", "http://localhost:8000")

app = FastAPI(title="Luleit PDF Editor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# ============ ANALYTICS STORAGE ============
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
    "daily_stats": defaultdict(lambda: {"uploads": 0, "downloads": 0, "unique_users": set()}),
    "hourly_stats": defaultdict(int),
    "user_sessions": {},  # ip -> session data
    "recent_activities": [],  # Last 100 activities
    "file_sizes": [],  # Track file sizes
    "page_counts": [],  # Track page counts
    "browsers": defaultdict(int),
    "countries": defaultdict(int),
    "referrers": defaultdict(int),
    "feature_usage": defaultdict(int),
    "errors": [],
}

pdf_storage = {}

# ============ HELPERS ============
def get_client_info(request: Request):
    ip = request.client.host
    ua = request.headers.get("user-agent", "Unknown")
    referer = request.headers.get("referer", "Direct")
    country = request.headers.get("cf-ipcountry", "Unknown")
    
    # Parse browser from user agent
    browser = "Other"
    if "Chrome" in ua: browser = "Chrome"
    elif "Firefox" in ua: browser = "Firefox"
    elif "Safari" in ua: browser = "Safari"
    elif "Edge" in ua: browser = "Edge"
    
    return {"ip": ip, "browser": browser, "country": country, "referer": referer, "ua": ua}

def track_activity(request: Request, action: str, details: str = ""):
    client = get_client_info(request)
    today = datetime.now().strftime("%Y-%m-%d")
    hour = datetime.now().strftime("%Y-%m-%d %H:00")
    
    # Update counters
    analytics["daily_stats"][today]["unique_users"].add(client["ip"])
    analytics["hourly_stats"][hour] += 1
    analytics["browsers"][client["browser"]] += 1
    analytics["countries"][client["country"]] += 1
    if client["referer"] != "Direct":
        domain = client["referer"].split("/")[2] if "/" in client["referer"] else client["referer"]
        analytics["referrers"][domain] += 1
    
    # Track user session
    if client["ip"] not in analytics["user_sessions"]:
        analytics["user_sessions"][client["ip"]] = {
            "first_seen": datetime.now().isoformat(),
            "actions": 0,
            "browser": client["browser"],
            "country": client["country"]
        }
    analytics["user_sessions"][client["ip"]]["last_seen"] = datetime.now().isoformat()
    analytics["user_sessions"][client["ip"]]["actions"] += 1
    
    # Add to recent activities
    activity = {
        "time": datetime.now().isoformat(),
        "action": action,
        "details": details,
        "ip": client["ip"][:10] + "***",  # Partial IP for privacy
        "country": client["country"],
        "browser": client["browser"]
    }
    analytics["recent_activities"].insert(0, activity)
    analytics["recent_activities"] = analytics["recent_activities"][:100]  # Keep last 100

def track_feature(feature: str):
    analytics["feature_usage"][feature] += 1

def extract_font_family(font_name):
    if not font_name: return "Arial, Helvetica, sans-serif"
    fl = font_name.lower()
    if "arial" in fl or "helvetica" in fl: return "Arial, Helvetica, sans-serif"
    elif "times" in fl: return "Times New Roman, Times, serif"
    elif "courier" in fl: return "Courier New, Courier, monospace"
    elif "georgia" in fl: return "Georgia, serif"
    else: return f"{font_name.split('-')[0]}, Arial, sans-serif"

def detect_page_format(w, h):
    formats = {"Letter":(612,792),"Legal":(612,1008),"A4":(595,842),"A3":(842,1191),"A5":(420,595)}
    best = "Custom"
    for name,(fw,fh) in formats.items():
        if min(abs(w-fw)+abs(h-fh), abs(w-fh)+abs(h-fw)) < 20: best = name; break
    return {"name":best,"width":w,"height":h,"orientation":"landscape" if w>h else "portrait"}

def generate_preview(content, max_pages=None):
    doc = fitz.open(stream=content, filetype="pdf")
    pages = []
    for i in range(min(len(doc), max_pages or len(doc))):
        page = doc[i]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        pages.append({"pageNum":i+1,"width":pix.width,"height":pix.height,"image":"data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode()})
    doc.close()
    return pages

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

# ============ MAIN ROUTES ============
@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html") as f: return f.read()

# ============ ADMIN PORTAL ============
@app.get("/admin", response_class=HTMLResponse)
def admin_portal(username: str = Depends(verify_admin)):
    return """
<!DOCTYPE html>
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
        .badge{background:var(--accent);padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600}
        .header-right{display:flex;align-items:center;gap:16px}
        .btn{padding:8px 16px;border:none;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:6px}
        .btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--bg3)}
        .btn-ghost:hover{background:var(--bg3);color:var(--text)}
        .main{padding:24px;max-width:1400px;margin:0 auto}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
        .stat-card{background:var(--bg2);border-radius:12px;padding:20px;border:1px solid var(--bg3)}
        .stat-card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
        .stat-card-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px}
        .stat-card-icon.purple{background:rgba(99,102,241,0.2);color:var(--accent)}
        .stat-card-icon.green{background:rgba(16,185,129,0.2);color:var(--success)}
        .stat-card-icon.orange{background:rgba(245,158,11,0.2);color:var(--warn)}
        .stat-card-icon.red{background:rgba(239,68,68,0.2);color:var(--danger)}
        .stat-card-value{font-size:32px;font-weight:700}
        .stat-card-label{font-size:13px;color:var(--text2);margin-top:4px}
        .stat-card-change{font-size:12px;margin-top:8px;display:flex;align-items:center;gap:4px}
        .stat-card-change.up{color:var(--success)}
        .stat-card-change.down{color:var(--danger)}
        .grid-2{display:grid;grid-template-columns:2fr 1fr;gap:24px;margin-bottom:24px}
        .card{background:var(--bg2);border-radius:12px;padding:20px;border:1px solid var(--bg3)}
        .card-title{font-size:16px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
        .card-title i{color:var(--accent)}
        .chart-container{height:300px;position:relative}
        .table{width:100%;border-collapse:collapse}
        .table th,.table td{padding:12px;text-align:left;border-bottom:1px solid var(--bg3)}
        .table th{color:var(--text2);font-weight:500;font-size:12px;text-transform:uppercase}
        .table td{font-size:13px}
        .table tr:hover{background:var(--bg3)}
        .activity-item{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--bg3)}
        .activity-item:last-child{border-bottom:none}
        .activity-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:14px}
        .activity-icon.upload{background:rgba(99,102,241,0.2);color:var(--accent)}
        .activity-icon.download{background:rgba(16,185,129,0.2);color:var(--success)}
        .activity-icon.merge{background:rgba(245,158,11,0.2);color:var(--warn)}
        .activity-icon.compress{background:rgba(236,72,153,0.2);color:#ec4899}
        .activity-info{flex:1}
        .activity-action{font-weight:500;font-size:13px}
        .activity-details{font-size:11px;color:var(--text2);margin-top:2px}
        .activity-time{font-size:11px;color:var(--text2)}
        .progress-bar{height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;margin-top:8px}
        .progress-fill{height:100%;border-radius:4px}
        .feature-item{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bg3)}
        .feature-item:last-child{border-bottom:none}
        .feature-name{font-size:13px;display:flex;align-items:center;gap:8px}
        .feature-count{font-weight:600;font-size:14px}
        .country-flag{font-size:20px;margin-right:8px}
        .tabs{display:flex;gap:8px;margin-bottom:16px}
        .tab{padding:8px 16px;background:var(--bg3);border:none;border-radius:6px;color:var(--text2);cursor:pointer;font-size:13px;font-family:inherit}
        .tab.active{background:var(--accent);color:#fff}
        .empty-state{text-align:center;padding:40px;color:var(--text2)}
        .live-dot{width:8px;height:8px;background:var(--success);border-radius:50%;animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
        @media(max-width:900px){.grid-2{grid-template-columns:1fr}}
    </style>
</head>
<body>
    <header class="header">
        <div class="logo"><i class="fas fa-chart-line"></i> Luleit Admin</div>
        <div class="header-right">
            <div style="display:flex;align-items:center;gap:8px"><div class="live-dot"></div><span style="font-size:12px;color:var(--text2)">Live</span></div>
            <button class="btn btn-ghost" onclick="location.reload()"><i class="fas fa-refresh"></i> Refresh</button>
            <button class="btn btn-ghost" onclick="location.href='/'"><i class="fas fa-external-link"></i> View Site</button>
        </div>
    </header>
    <main class="main">
        <div class="stats-grid" id="stats-grid"></div>
        <div class="grid-2">
            <div class="card">
                <div class="card-title"><i class="fas fa-chart-area"></i> Usage Over Time</div>
                <div class="chart-container"><canvas id="usageChart"></canvas></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-bolt"></i> Recent Activity</div>
                <div id="recent-activity"></div>
            </div>
        </div>
        <div class="grid-2">
            <div class="card">
                <div class="card-title"><i class="fas fa-toolbox"></i> Feature Usage</div>
                <div id="feature-usage"></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-globe"></i> Top Countries</div>
                <div id="countries"></div>
            </div>
        </div>
        <div class="grid-2">
            <div class="card">
                <div class="card-title"><i class="fas fa-browser"></i> Browsers</div>
                <div class="chart-container" style="height:200px"><canvas id="browserChart"></canvas></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fas fa-link"></i> Top Referrers</div>
                <div id="referrers"></div>
            </div>
        </div>
        <div class="card">
            <div class="card-title"><i class="fas fa-users"></i> User Sessions</div>
            <table class="table">
                <thead><tr><th>IP (Partial)</th><th>Country</th><th>Browser</th><th>Actions</th><th>First Seen</th><th>Last Seen</th></tr></thead>
                <tbody id="sessions-table"></tbody>
            </table>
        </div>
    </main>
    <script>
        async function loadData() {
            const res = await fetch('/admin/api/stats');
            const data = await res.json();
            
            // Stats cards
            document.getElementById('stats-grid').innerHTML = `
                <div class="stat-card">
                    <div class="stat-card-header">
                        <div class="stat-card-icon purple"><i class="fas fa-upload"></i></div>
                    </div>
                    <div class="stat-card-value">${data.total_uploads}</div>
                    <div class="stat-card-label">Total Uploads</div>
                </div>
                <div class="stat-card">
                    <div class="stat-card-header">
                        <div class="stat-card-icon green"><i class="fas fa-download"></i></div>
                    </div>
                    <div class="stat-card-value">${data.total_downloads}</div>
                    <div class="stat-card-label">Total Downloads</div>
                </div>
                <div class="stat-card">
                    <div class="stat-card-header">
                        <div class="stat-card-icon orange"><i class="fas fa-users"></i></div>
                    </div>
                    <div class="stat-card-value">${data.unique_users}</div>
                    <div class="stat-card-label">Unique Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-card-header">
                        <div class="stat-card-icon red"><i class="fas fa-clock"></i></div>
                    </div>
                    <div class="stat-card-value">${data.today_uploads}</div>
                    <div class="stat-card-label">Today's Uploads</div>
                </div>
            `;
            
            // Recent activity
            const activityHtml = data.recent_activities.slice(0,10).map(a => `
                <div class="activity-item">
                    <div class="activity-icon ${a.action.toLowerCase()}""><i class="fas fa-${getIcon(a.action)}"></i></div>
                    <div class="activity-info">
                        <div class="activity-action">${a.action}</div>
                        <div class="activity-details">${a.details || ''} • ${a.country} • ${a.browser}</div>
                    </div>
                    <div class="activity-time">${timeAgo(a.time)}</div>
                </div>
            `).join('') || '<div class="empty-state">No activity yet</div>';
            document.getElementById('recent-activity').innerHTML = activityHtml;
            
            // Feature usage
            const features = Object.entries(data.feature_usage).sort((a,b) => b[1] - a[1]);
            const maxFeature = Math.max(...features.map(f => f[1]), 1);
            const featureHtml = features.map(([name, count]) => `
                <div class="feature-item">
                    <div class="feature-name"><i class="fas fa-${getFeatureIcon(name)}"></i> ${name}</div>
                    <div class="feature-count">${count}</div>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width:${(count/maxFeature)*100}%;background:var(--accent)"></div></div>
            `).join('') || '<div class="empty-state">No data yet</div>';
            document.getElementById('feature-usage').innerHTML = featureHtml;
            
            // Countries
            const countries = Object.entries(data.countries).sort((a,b) => b[1] - a[1]).slice(0,10);
            const countriesHtml = countries.map(([code, count]) => `
                <div class="feature-item">
                    <div class="feature-name">${getFlag(code)} ${code}</div>
                    <div class="feature-count">${count}</div>
                </div>
            `).join('') || '<div class="empty-state">No data yet</div>';
            document.getElementById('countries').innerHTML = countriesHtml;
            
            // Referrers
            const referrers = Object.entries(data.referrers).sort((a,b) => b[1] - a[1]).slice(0,5);
            const referrersHtml = referrers.map(([domain, count]) => `
                <div class="feature-item">
                    <div class="feature-name"><i class="fas fa-link"></i> ${domain}</div>
                    <div class="feature-count">${count}</div>
                </div>
            `).join('') || '<div class="empty-state">No referrers yet</div>';
            document.getElementById('referrers').innerHTML = referrersHtml;
            
            // Sessions table
            const sessions = Object.entries(data.user_sessions).slice(0,20);
            const sessionsHtml = sessions.map(([ip, s]) => `
                <tr>
                    <td>${ip.substring(0,10)}***</td>
                    <td>${s.country}</td>
                    <td>${s.browser}</td>
                    <td>${s.actions}</td>
                    <td>${timeAgo(s.first_seen)}</td>
                    <td>${timeAgo(s.last_seen)}</td>
                </tr>
            `).join('') || '<tr><td colspan="6" style="text-align:center">No sessions yet</td></tr>';
            document.getElementById('sessions-table').innerHTML = sessionsHtml;
            
            // Usage chart
            const dailyData = Object.entries(data.daily_stats).slice(-7);
            new Chart(document.getElementById('usageChart'), {
                type: 'line',
                data: {
                    labels: dailyData.map(d => d[0].slice(5)),
                    datasets: [{
                        label: 'Uploads',
                        data: dailyData.map(d => d[1].uploads),
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99,102,241,0.1)',
                        fill: true,
                        tension: 0.4
                    }, {
                        label: 'Downloads',
                        data: dailyData.map(d => d[1].downloads),
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16,185,129,0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#94a3b8' } } },
                    scales: {
                        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
                    }
                }
            });
            
            // Browser chart
            const browsers = Object.entries(data.browsers);
            new Chart(document.getElementById('browserChart'), {
                type: 'doughnut',
                data: {
                    labels: browsers.map(b => b[0]),
                    datasets: [{
                        data: browsers.map(b => b[1]),
                        backgroundColor: ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { position: 'right', labels: { color: '#94a3b8' } } }
                }
            });
        }
        
        function getIcon(action) {
            const icons = { Upload: 'upload', Download: 'download', Merge: 'layer-group', Compress: 'compress', Split: 'cut', Convert: 'exchange-alt', Protect: 'lock', Edit: 'edit' };
            return icons[action] || 'circle';
        }
        
        function getFeatureIcon(name) {
            const icons = { upload: 'upload', download: 'download', merge: 'layer-group', compress: 'compress', split: 'cut', convert: 'exchange-alt', protect: 'lock', edit: 'edit', watermark: 'tint', rotate: 'sync', 'page-numbers': 'list-ol', signature: 'signature' };
            return icons[name.toLowerCase()] || 'circle';
        }
        
        function getFlag(code) {
            if (!code || code === 'Unknown') return '🌍';
            return String.fromCodePoint(...[...code.toUpperCase()].map(c => 127397 + c.charCodeAt(0)));
        }
        
        function timeAgo(dateStr) {
            const date = new Date(dateStr);
            const now = new Date();
            const diff = Math.floor((now - date) / 1000);
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff/60) + 'm ago';
            if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
            return Math.floor(diff/86400) + 'd ago';
        }
        
        loadData();
        setInterval(loadData, 30000); // Refresh every 30s
    </script>
</body>
</html>
"""

@app.get("/admin/api/stats")
def admin_stats(username: str = Depends(verify_admin)):
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Convert sets to counts for JSON serialization
    daily_stats_serializable = {}
    for date, stats in analytics["daily_stats"].items():
        daily_stats_serializable[date] = {
            "uploads": stats["uploads"],
            "downloads": stats["downloads"],
            "unique_users": len(stats["unique_users"])
        }
    
    return {
        "total_uploads": analytics["total_uploads"],
        "total_downloads": analytics["total_downloads"],
        "total_merges": analytics["total_merges"],
        "total_splits": analytics["total_splits"],
        "total_compressions": analytics["total_compressions"],
        "total_conversions": analytics["total_conversions"],
        "unique_users": len(analytics["user_sessions"]),
        "today_uploads": analytics["daily_stats"][today]["uploads"],
        "today_downloads": analytics["daily_stats"][today]["downloads"],
        "today_users": len(analytics["daily_stats"][today]["unique_users"]),
        "daily_stats": daily_stats_serializable,
        "hourly_stats": dict(analytics["hourly_stats"]),
        "browsers": dict(analytics["browsers"]),
        "countries": dict(analytics["countries"]),
        "referrers": dict(analytics["referrers"]),
        "feature_usage": dict(analytics["feature_usage"]),
        "recent_activities": analytics["recent_activities"][:50],
        "user_sessions": {k: v for k, v in list(analytics["user_sessions"].items())[:50]},
        "errors": analytics["errors"][-20:]
    }

# ============ PDF ROUTES ============
@app.post("/upload-pdf")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 50*1024*1024: raise HTTPException(400, "File too large (max 50MB)")
    try: doc = fitz.open(stream=content, filetype="pdf")
    except: raise HTTPException(400, "Invalid PDF")
    
    # Track analytics
    analytics["total_uploads"] += 1
    today = datetime.now().strftime("%Y-%m-%d")
    analytics["daily_stats"][today]["uploads"] += 1
    analytics["file_sizes"].append(len(content))
    analytics["page_counts"].append(len(doc))
    track_activity(request, "Upload", f"{file.filename} ({len(doc)} pages)")
    track_feature("upload")
    
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content":content,"filename":file.filename,"created":datetime.now()}
    
    fonts_used, sizes_used, colors_used = {}, {}, {}
    pages_data = []
    
    for pn in range(len(doc)):
        page = doc[pn]
        mat = fitz.Matrix(2.5, 2.5)
        pix = page.get_pixmap(matrix=mat)
        
        text_blocks = []
        for block in page.get_text("dict")["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"].strip():
                            fn = span.get("font","Arial")
                            fs = round(span.get("size",12),1)
                            ci = span.get("color",0)
                            ch = "#{:06x}".format(ci) if ci else "#000000"
                            fonts_used[fn] = fonts_used.get(fn,0)+1
                            sizes_used[fs] = sizes_used.get(fs,0)+1
                            colors_used[ch] = colors_used.get(ch,0)+1
                            text_blocks.append({"text":span["text"],"x":span["bbox"][0]*2.5,"y":span["bbox"][1]*2.5,"width":(span["bbox"][2]-span["bbox"][0])*2.5,"height":(span["bbox"][3]-span["bbox"][1])*2.5,"fontSize":span["size"]*2.5,"fontName":fn,"fontFamily":extract_font_family(fn),"color":ch,"originalX":span["bbox"][0],"originalY":span["bbox"][1],"originalWidth":span["bbox"][2]-span["bbox"][0],"originalHeight":span["bbox"][3]-span["bbox"][1],"originalFontSize":span["size"]})
        
        pages_data.append({"pageNum":pn+1,"width":pix.width,"height":pix.height,"originalWidth":page.rect.width,"originalHeight":page.rect.height,"rotation":page.rotation,"image":"data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode(),"textBlocks":text_blocks})
    
    fs = len(content)
    doc.close()
    return {"pdfId":pdf_id,"pageCount":len(pages_data),"pages":pages_data,"fileName":file.filename,"fileSize":f"{fs/1024:.1f} KB" if fs<1024*1024 else f"{fs/1024/1024:.1f} MB","documentStyle":{"fonts":list(fonts_used.keys()),"fontFamilies":list(set(extract_font_family(f) for f in fonts_used)),"sizes":sorted(sizes_used.keys()),"colors":list(colors_used.keys())[:10],"defaultFont":max(fonts_used,key=fonts_used.get) if fonts_used else "Arial","defaultFontFamily":extract_font_family(max(fonts_used,key=fonts_used.get) if fonts_used else "Arial"),"defaultSize":max(sizes_used,key=sizes_used.get) if sizes_used else 12,"defaultColor":max(colors_used,key=colors_used.get) if colors_used else "#000000","pageFormat":detect_page_format(pages_data[0]["originalWidth"],pages_data[0]["originalHeight"]) if pages_data else {}}}

@app.post("/merge-pdfs")
async def merge_pdfs(request: Request, files: List[UploadFile] = File(...)):
    if len(files) < 2: raise HTTPException(400, "Need 2+ files")
    merged = fitz.open()
    for f in files:
        content = await f.read()
        pdf = fitz.open(stream=content, filetype="pdf")
        merged.insert_pdf(pdf)
        pdf.close()
    
    analytics["total_merges"] += 1
    track_activity(request, "Merge", f"{len(files)} files")
    track_feature("merge")
    
    pdf_id = str(uuid.uuid4())
    output = io.BytesIO()
    merged.save(output)
    merged.close()
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content":content,"filename":"merged.pdf","created":datetime.now()}
    
    return {"pdfId":pdf_id,"pageCount":fitz.open(stream=content,filetype="pdf").page_count,"pages":generate_preview(content),"fileName":"merged.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

@app.post("/extract-pages")
async def extract_pages(request: Request, pdf_id: str = Form(...), pages: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page_list = json.loads(pages)
    
    analytics["total_splits"] += 1
    track_activity(request, "Split", f"{len(page_list)} pages extracted")
    track_feature("split")
    
    new_doc = fitz.open()
    for p in page_list:
        if 1 <= p <= len(doc):
            new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)
    
    out = io.BytesIO()
    new_doc.save(out)
    new_doc.close()
    doc.close()
    
    new_id = str(uuid.uuid4())
    content = out.getvalue()
    pdf_storage[new_id] = {"content":content,"filename":"extracted.pdf","created":datetime.now()}
    
    return {"pdfId":new_id,"pageCount":len(page_list),"pages":generate_preview(content),"fileName":"extracted.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

@app.post("/compress-pdf")
async def compress_pdf(request: Request, file: UploadFile = File(...), quality: str = Form("medium")):
    content = await file.read()
    original_size = len(content)
    
    doc = fitz.open(stream=content, filetype="pdf")
    
    if Image:
        image_quality = {"high":90,"medium":70,"low":50}.get(quality, 70)
        for page in doc:
            for img in page.get_images():
                try:
                    xref = img[0]
                    base = doc.extract_image(xref)
                    img_bytes = base["image"]
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    if pil_img.mode in ('RGBA','P'): pil_img = pil_img.convert('RGB')
                    if quality == "low" and max(pil_img.size) > 1200:
                        ratio = 1200 / max(pil_img.size)
                        pil_img = pil_img.resize((int(pil_img.width*ratio), int(pil_img.height*ratio)), Image.LANCZOS)
                    out = io.BytesIO()
                    pil_img.save(out, format='JPEG', quality=image_quality, optimize=True)
                    page.replace_image(xref, stream=out.getvalue())
                except: pass
    
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True, clean=True)
    doc.close()
    
    compressed = output.getvalue()
    
    analytics["total_compressions"] += 1
    track_activity(request, "Compress", f"{original_size//1024}KB → {len(compressed)//1024}KB")
    track_feature("compress")
    
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content":compressed,"filename":f"compressed_{file.filename}","created":datetime.now()}
    
    return {"pdfId":pdf_id,"originalSize":f"{original_size/1024:.1f} KB","compressedSize":f"{len(compressed)/1024:.1f} KB","savings":f"{((original_size-len(compressed))/original_size)*100:.1f}%","fileName":f"compressed_{file.filename}"}

@app.post("/page-operations")
async def page_operations(request: Request, pdf_id: str = Form(...), operations: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    ops = json.loads(operations)
    
    for op in ops:
        if op["type"] == "rotate" and 0 <= op["page"]-1 < len(doc):
            doc[op["page"]-1].set_rotation(doc[op["page"]-1].rotation + op["angle"])
            track_feature("rotate")
    
    for p in sorted([op["page"]-1 for op in ops if op["type"]=="delete"], reverse=True):
        if 0 <= p < len(doc) and len(doc) > 1: doc.delete_page(p)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    track_activity(request, "Edit", "Page operations")
    
    return {"success":True,"pageCount":fitz.open(stream=content,filetype="pdf").page_count,"pages":generate_preview(content)}

@app.post("/add-watermark")
async def add_watermark(request: Request, pdf_id: str = Form(...), text: str = Form(""), position: str = Form("center"), rotation: float = Form(-45)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    for page in doc:
        rect = page.rect
        fontsize = min(rect.width, rect.height) / 8
        tw = fitz.get_text_length(text, fontsize=fontsize)
        x, y = rect.width/2-tw/2, rect.height/2
        page.insert_text(fitz.Point(x,y), text, fontsize=fontsize, color=(0.7,0.7,0.7), rotate=rotation)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    analytics["total_watermarks"] += 1
    track_activity(request, "Watermark", text)
    track_feature("watermark")
    
    return {"success":True,"pages":generate_preview(content)}

@app.post("/add-page-numbers")
async def add_page_numbers(request: Request, pdf_id: str = Form(...), position: str = Form("bottom-center"), start_num: int = Form(1), format_str: str = Form("Page {n} of {total}")):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    total = len(doc)
    
    for i, page in enumerate(doc):
        rect = page.rect
        text = format_str.replace("{n}", str(start_num+i)).replace("{total}", str(total))
        tw = fitz.get_text_length(text, fontsize=10)
        y = rect.height - 30 if "bottom" in position else 40
        x = (rect.width - tw) / 2 if "center" in position else (50 if "left" in position else rect.width - tw - 50)
        page.insert_text(fitz.Point(x,y), text, fontsize=10, color=(0,0,0))
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    analytics["total_page_numbers"] += 1
    track_activity(request, "Page Numbers", format_str)
    track_feature("page-numbers")
    
    return {"success":True,"pages":generate_preview(content)}

@app.post("/protect-pdf")
async def protect_pdf(request: Request, pdf_id: str = Form(...), password: str = Form(...), permissions: str = Form("all")):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    perm = fitz.PDF_PERM_ACCESSIBILITY
    if permissions == "all": perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_MODIFY
    elif permissions == "print": perm |= fitz.PDF_PERM_PRINT
    
    output = io.BytesIO()
    doc.save(output, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, permissions=perm)
    doc.close()
    
    new_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[new_id] = {"content":content,"filename":"protected.pdf","created":datetime.now()}
    
    analytics["total_protects"] += 1
    track_activity(request, "Protect", "Password added")
    track_feature("protect")
    
    return {"pdfId":new_id,"fileName":"protected.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

@app.post("/unlock-pdf")
async def unlock_pdf(request: Request, file: UploadFile = File(...), password: str = Form(...)):
    content = await file.read()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        if doc.is_encrypted and not doc.authenticate(password):
            raise HTTPException(400, "Wrong password")
        
        output = io.BytesIO()
        doc.save(output)
        doc.close()
        
        new_id = str(uuid.uuid4())
        unlocked = output.getvalue()
        pdf_storage[new_id] = {"content":unlocked,"filename":"unlocked.pdf","created":datetime.now()}
        
        analytics["total_unlocks"] += 1
        track_activity(request, "Unlock", "Password removed")
        track_feature("unlock")
        
        return {"pdfId":new_id,"pages":generate_preview(unlocked),"fileName":"unlocked.pdf"}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/pdf-to-images")
async def pdf_to_images(request: Request, file: UploadFile = File(...), format: str = Form("png"), dpi: int = Form(150)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    images = []
    mat = fitz.Matrix(dpi/72, dpi/72)
    
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        if format == "jpg" and Image:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=90)
            img_bytes = out.getvalue()
            mime = "image/jpeg"
        else:
            img_bytes = pix.tobytes("png")
            mime = "image/png"
        images.append({"id":str(uuid.uuid4()),"page":i+1,"data":"data:"+mime+";base64,"+base64.b64encode(img_bytes).decode(),"filename":f"page_{i+1}.{format}"})
    
    doc.close()
    
    analytics["total_conversions"] += 1
    track_activity(request, "Convert", f"PDF to {format.upper()}")
    track_feature("convert")
    
    return {"images":images}

@app.post("/images-to-pdf")
async def images_to_pdf(request: Request, files: List[UploadFile] = File(...), page_size: str = Form("A4")):
    sizes = {"A4":(595,842),"Letter":(612,792),"Legal":(612,1008)}
    w, h = sizes.get(page_size, (595,842))
    
    doc = fitz.open()
    
    for f in files:
        img_bytes = await f.read()
        if Image:
            img = Image.open(io.BytesIO(img_bytes))
            iw, ih = img.size
            scale = min(w/iw, h/ih) * 0.9
            nw, nh = iw*scale, ih*scale
            page = doc.new_page(width=w, height=h)
            x, y = (w-nw)/2, (h-nh)/2
            out = io.BytesIO()
            if img.mode == 'RGBA': img = img.convert('RGB')
            img.save(out, format='JPEG', quality=90)
            page.insert_image(fitz.Rect(x,y,x+nw,y+nh), stream=out.getvalue())
        else:
            page = doc.new_page(width=w, height=h)
            page.insert_image(fitz.Rect(50,50,w-50,h-50), stream=img_bytes)
    
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    
    pdf_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content":content,"filename":"images.pdf","created":datetime.now()}
    
    analytics["total_conversions"] += 1
    track_activity(request, "Convert", f"{len(files)} images to PDF")
    track_feature("convert")
    
    return {"pdfId":pdf_id,"pageCount":len(files),"pages":generate_preview(content),"fileName":"images.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

@app.post("/rotate-all")
async def rotate_all(request: Request, pdf_id: str = Form(...), angle: int = Form(90)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    for page in doc:
        page.set_rotation(page.rotation + angle)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    analytics["total_rotations"] += 1
    track_activity(request, "Rotate", f"All pages {angle}°")
    track_feature("rotate")
    
    return {"success":True,"pages":generate_preview(content)}

# ============ FREE DOWNLOAD (NO PAYMENT) ============
@app.get("/download/{pdf_id}")
async def download_pdf(request: Request, pdf_id: str):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    s = pdf_storage[pdf_id]
    
    analytics["total_downloads"] += 1
    today = datetime.now().strftime("%Y-%m-%d")
    analytics["daily_stats"][today]["downloads"] += 1
    track_activity(request, "Download", s["filename"])
    track_feature("download")
    
    return StreamingResponse(io.BytesIO(s["content"]), media_type="application/pdf", headers={"Content-Disposition":f"attachment; filename={s['filename']}"})

@app.get("/download-direct/{pdf_id}")
async def download_direct(request: Request, pdf_id: str):
    return await download_pdf(request, pdf_id)

# ============ SAVE EDITS & DOWNLOAD (FREE) ============
@app.post("/save-and-download")
async def save_and_download(request: Request, pdf_id: str = Form(...), edits: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    
    content = pdf_storage[pdf_id]["content"]
    edit_list = json.loads(edits)
    
    if edit_list:
        doc = fitz.open(stream=content, filetype="pdf")
        for e in edit_list:
            page = doc[e["pageNum"]-1]
            rect = fitz.Rect(e["originalX"]-1, e["originalY"]-1, e["originalX"]+e["originalWidth"]+5, e["originalY"]+e["originalHeight"]+1)
            page.draw_rect(rect, color=(1,1,1), fill=(1,1,1))
            if e.get("newText"):
                page.insert_text(fitz.Point(e["originalX"], e["originalY"]+e["originalHeight"]-2), e["newText"], fontsize=e["originalFontSize"])
        out = io.BytesIO()
        doc.save(out)
        doc.close()
        content = out.getvalue()
    
    analytics["total_downloads"] += 1
    analytics["total_edits"] += len(edit_list)
    today = datetime.now().strftime("%Y-%m-%d")
    analytics["daily_stats"][today]["downloads"] += 1
    track_activity(request, "Download", f"With {len(edit_list)} edits")
    track_feature("download")
    track_feature("edit")
    
    return StreamingResponse(io.BytesIO(content), media_type="application/pdf", headers={"Content-Disposition":"attachment; filename=luleit-edited.pdf"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
