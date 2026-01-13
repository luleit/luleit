"""
Luleit PDF Editor - Production Grade v3.0
TRUE PDF Editing with PyMuPDF (not annotation overlays)
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import fitz
import os
import uuid
import json
import stripe
import base64
from datetime import datetime, timedelta
from typing import List
import httpx
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="Luleit PDF Editor", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/tmp/luleit_uploads"
OUTPUT_DIR = "/tmp/luleit_outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")

file_storage = {}
download_tokens = {}

# Pricing
COUNTRY_PRICING = {
    "US": 100, "CH": 120, "GB": 95, "DE": 95, "AU": 100, "CA": 90,
    "AE": 85, "SA": 80, "SG": 100, "JP": 90, "FR": 85, "IT": 80,
    "IN": 15, "PK": 12, "BD": 10, "ID": 20, "PH": 25, "BR": 35, "MX": 40,
}

async def calculate_price(request: Request, edit_count: int = 0, time_spent: int = 0, features: list = None) -> dict:
    base = 2.99
    features = features or []
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    if "," in str(client_ip): client_ip = client_ip.split(",")[0].strip()
    
    country = "US"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"http://ip-api.com/json/{client_ip}")
            if r.status_code == 200: country = r.json().get("countryCode", "US")
    except: pass
    
    mult = COUNTRY_PRICING.get(country, 70) / 100
    premium = 0
    if edit_count >= 10: premium += 15
    if time_spent >= 300: premium += 10
    if "signature" in features: premium += 10
    
    ua = request.headers.get("user-agent", "").lower()
    if "iphone" in ua or "macintosh" in ua: premium += 10
    
    price = base * mult * (1 + premium/100)
    price = max(0.25, min(5.99, round(price, 2)))
    return {"price": price, "currency": "usd", "country": country}


def extract_text_with_positions(page: fitz.Page) -> list:
    """Extract all text spans with exact positions for editing"""
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0: continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip(): continue
                bbox = span.get("bbox", [0,0,0,0])
                blocks.append({
                    "id": f"t{len(blocks)}",
                    "type": "existing_text",
                    "text": text,
                    "originalText": text,
                    "x": bbox[0],
                    "y": bbox[1],
                    "width": bbox[2] - bbox[0],
                    "height": bbox[3] - bbox[1],
                    "fontSize": span.get("size", 12),
                    "fontFamily": span.get("font", "Helvetica"),
                    "color": "#{:06x}".format(span.get("color", 0)),
                })
    return blocks


def apply_edits_to_page(page: fitz.Page, edits: list, scale: float):
    """Apply all edits to a page using proper PDF modification"""
    
    # Separate edit types
    text_replacements = []
    new_texts = []
    images = []
    drawings = []
    highlights = []
    
    for edit in edits:
        t = edit.get("type")
        if t == "existing_text":
            orig = edit.get("originalText", "")
            new = edit.get("text", "")
            if orig and new and orig != new:
                text_replacements.append(edit)
        elif t == "text":
            new_texts.append(edit)
        elif t == "image":
            images.append(edit)
        elif t == "draw":
            drawings.append(edit)
        elif t == "highlight":
            highlights.append(edit)
    
    # 1. Apply text replacements using redaction (REAL PDF text modification)
    for edit in text_replacements:
        orig = edit.get("originalText", "")
        new = edit.get("text", "")
        rects = page.search_for(orig)
        
        if rects:
            font_size = edit.get("fontSize", 12) / scale
            color_hex = edit.get("color", "#000000").lstrip("#")
            color = tuple(int(color_hex[i:i+2], 16)/255 for i in (0,2,4))
            
            for rect in rects:
                page.add_redact_annot(
                    rect,
                    text=new,
                    fontsize=font_size,
                    fontname="helv",
                    text_color=color,
                    fill=(1,1,1),
                )
    
    # Apply all redactions at once
    if text_replacements:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    
    # 2. Add new text
    for edit in new_texts:
        text = edit.get("text", "").strip()
        if not text or text == "Type here...": continue
        
        x = edit.get("x", 0) / scale
        y = edit.get("y", 0) / scale
        font_size = edit.get("fontSize", 12) / scale
        color_hex = edit.get("color", "#000000").lstrip("#")
        color = tuple(int(color_hex[i:i+2], 16)/255 for i in (0,2,4))
        
        page.insert_text(
            (x, y + font_size),
            text,
            fontsize=font_size,
            fontname="helv",
            color=color,
        )
    
    # 3. Add images
    for edit in images:
        img_data = edit.get("data", "")
        if not img_data: continue
        if img_data.startswith("data:"): img_data = img_data.split(",",1)[1]
        
        try:
            img_bytes = base64.b64decode(img_data)
            x = edit.get("x", 0) / scale
            y = edit.get("y", 0) / scale
            w = edit.get("width", 100) / scale
            h = edit.get("height", 100) / scale
            rect = fitz.Rect(x, y, x+w, y+h)
            page.insert_image(rect, stream=img_bytes)
        except: pass
    
    # 4. Add drawings
    for edit in drawings:
        paths = edit.get("paths", [])
        for path in paths:
            points = path.get("points", [])
            if len(points) < 2: continue
            
            color_hex = path.get("color", "#DA2D26").lstrip("#")
            color = tuple(int(color_hex[i:i+2], 16)/255 for i in (0,2,4))
            width = path.get("width", 2) / scale
            
            shape = page.new_shape()
            scaled_points = [(p["x"]/scale, p["y"]/scale) for p in points]
            shape.draw_polyline(scaled_points)
            shape.finish(color=color, width=width, lineCap=1, lineJoin=1)
            shape.commit()
    
    # 5. Add highlights
    for edit in highlights:
        x = edit.get("x", 0) / scale
        y = edit.get("y", 0) / scale
        w = edit.get("width", 100) / scale
        h = edit.get("height", 20) / scale
        
        rect = fitz.Rect(x, y, x+w, y+h)
        annot = page.add_highlight_annot(rect)
        
        color_hex = edit.get("color", "#ffff00").lstrip("#")
        color = tuple(int(color_hex[i:i+2], 16)/255 for i in (0,2,4))
        annot.set_colors(stroke=color)
        annot.update()


# ==================== ROUTES ====================

@app.get("/", response_class=HTMLResponse)
async def home():
    with open(TEMPLATES_DIR / "index.html") as f: return f.read()

@app.get("/edit", response_class=HTMLResponse)
@app.get("/editor", response_class=HTMLResponse)
async def editor():
    with open(TEMPLATES_DIR / "editor.html") as f: return f.read()

@app.get("/merge", response_class=HTMLResponse)
async def merge_page():
    with open(TEMPLATES_DIR / "merge.html") as f: return f.read()

@app.get("/compress", response_class=HTMLResponse)
async def compress_page():
    with open(TEMPLATES_DIR / "compress.html") as f: return f.read()

@app.get("/sign", response_class=HTMLResponse)
async def sign_page():
    with open(TEMPLATES_DIR / "sign.html") as f: return f.read()

@app.get("/api/health")
async def health():
    return {"status": "healthy", "version": "3.0.0", "pymupdf": fitz.version[0]}

@app.get("/api/config")
async def get_config():
    return {
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "stripe_enabled": bool(STRIPE_PUBLISHABLE_KEY and stripe.api_key),
    }


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload PDF and extract editable content"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Only PDF files allowed")
    
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")
    
    file_id = str(uuid.uuid4())
    scale = 1.5
    
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        
        # Store original
        file_storage[file_id] = {"bytes": content, "name": file.filename, "scale": scale}
        with open(os.path.join(UPLOAD_DIR, f"{file_id}.pdf"), "wb") as f:
            f.write(content)
        
        pages = []
        for i, page in enumerate(doc):
            # Render image
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()
            
            # Extract editable text
            text_blocks = extract_text_with_positions(page)
            
            pages.append({
                "pageNumber": i + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "image": f"data:image/png;base64,{img_b64}",
                "textBlocks": text_blocks,
                "scale": scale,
            })
        
        doc.close()
        
        return {
            "fileId": file_id,
            "fileName": file.filename,
            "pageCount": len(pages),
            "pages": pages,
            "scale": scale,
        }
    except Exception as e:
        raise HTTPException(500, f"Error processing PDF: {str(e)}")


@app.post("/api/save")
async def save_pdf(file_id: str = Form(...), edits: str = Form(...)):
    """Apply edits and save - REAL PDF modification"""
    
    # Get original PDF
    if file_id not in file_storage:
        path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
        if not os.path.exists(path):
            raise HTTPException(404, "File not found")
        with open(path, "rb") as f:
            file_storage[file_id] = {"bytes": f.read(), "name": "document.pdf", "scale": 1.5}
    
    try:
        edits_data = json.loads(edits)
        original = file_storage[file_id]["bytes"]
        scale = file_storage[file_id].get("scale", 1.5)
        
        doc = fitz.open(stream=original, filetype="pdf")
        
        # Group by page
        by_page = {}
        for edit in edits_data:
            p = edit.get("page", 1) - 1
            if p not in by_page: by_page[p] = []
            by_page[p].append(edit)
        
        # Apply edits
        for page_num, page_edits in by_page.items():
            if 0 <= page_num < len(doc):
                apply_edits_to_page(doc[page_num], page_edits, scale)
        
        # Save with proper settings
        output_path = os.path.join(OUTPUT_DIR, f"{file_id}_edited.pdf")
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        
        return {"fileId": file_id, "status": "saved"}
    except Exception as e:
        raise HTTPException(500, f"Error saving: {str(e)}")


@app.post("/api/merge")
async def merge_pdfs(files: List[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(400, "Need at least 2 files")
    
    merged = fitz.open()
    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        merged.insert_pdf(doc)
        doc.close()
    
    output_id = str(uuid.uuid4())
    output_path = os.path.join(OUTPUT_DIR, f"{output_id}_merged.pdf")
    merged.save(output_path, garbage=4, deflate=True)
    merged.close()
    
    return {"fileId": output_id}


@app.post("/api/compress")
async def compress_pdf(file: UploadFile = File(...)):
    content = await file.read()
    original_size = len(content)
    
    doc = fitz.open(stream=content, filetype="pdf")
    output_id = str(uuid.uuid4())
    output_path = os.path.join(OUTPUT_DIR, f"{output_id}_compressed.pdf")
    
    doc.save(output_path, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    doc.close()
    
    compressed_size = os.path.getsize(output_path)
    return {
        "fileId": output_id,
        "originalSize": original_size,
        "compressedSize": compressed_size,
        "reduction": round((1 - compressed_size/original_size) * 100, 1),
    }


@app.post("/api/price")
async def get_price_endpoint(request: Request, edit_count: int = Form(0), time_spent: int = Form(0), features: str = Form("[]")):
    return await calculate_price(request, edit_count, time_spent, json.loads(features) if features else [])


@app.post("/api/create-payment")
async def create_payment(request: Request, file_id: str = Form(...), edit_count: int = Form(0), time_spent: int = Form(0), features: str = Form("[]")):
    if not stripe.api_key:
        raise HTTPException(400, "Payments not configured")
    
    pricing = await calculate_price(request, edit_count, time_spent, json.loads(features) if features else [])
    intent = stripe.PaymentIntent.create(amount=int(pricing["price"]*100), currency="usd", metadata={"file_id": file_id})
    return {"clientSecret": intent.client_secret, "price": pricing["price"]}


@app.post("/api/verify-payment")
async def verify_payment(payment_intent_id: str = Form(...), file_id: str = Form(...)):
    token = str(uuid.uuid4())
    download_tokens[token] = {"file_id": file_id, "expires": datetime.now() + timedelta(hours=24)}
    
    if not stripe.api_key or payment_intent_id == "free":
        return {"token": token}
    
    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    if intent.status == "succeeded":
        return {"token": token}
    raise HTTPException(400, "Payment not completed")


@app.get("/api/download/{token}")
async def download_pdf(token: str):
    if token not in download_tokens:
        raise HTTPException(403, "Invalid token")
    
    data = download_tokens[token]
    if datetime.now() > data["expires"]:
        del download_tokens[token]
        raise HTTPException(403, "Expired")
    
    file_id = data["file_id"]
    for suffix in ["_edited.pdf", "_merged.pdf", "_compressed.pdf"]:
        path = os.path.join(OUTPUT_DIR, f"{file_id}{suffix}")
        if os.path.exists(path):
            return FileResponse(path, media_type="application/pdf", filename="luleit_document.pdf")
    
    raise HTTPException(404, "File not found")


static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
