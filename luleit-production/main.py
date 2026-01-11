from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import fitz  # PyMuPDF
import os, json, base64, uuid, io, math, secrets, httpx
from datetime import datetime
from typing import List, Optional
from collections import defaultdict

try:
    from PIL import Image
except:
    Image = None

try:
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_ENABLED = bool(stripe.api_key)
    print(f"Stripe enabled: {STRIPE_ENABLED}, Key present: {bool(stripe.api_key)}")
except Exception as e:
    print(f"Stripe import error: {e}")
    STRIPE_ENABLED = False

# ============ CONFIG ============
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "luleit2024")
BASE_PRICE_USD_CENTS = 299

# Simplified pricing for now
COUNTRY_PRICING = {
    "US": {"currency": "usd", "symbol": "$", "multiplier": 1.0},
    "GB": {"currency": "gbp", "symbol": "£", "multiplier": 0.85},
    "DE": {"currency": "eur", "symbol": "€", "multiplier": 0.9},
    "FR": {"currency": "eur", "symbol": "€", "multiplier": 0.9},
    "AU": {"currency": "aud", "symbol": "A$", "multiplier": 1.1},
    "CA": {"currency": "cad", "symbol": "C$", "multiplier": 1.0},
    "AE": {"currency": "aed", "symbol": "AED", "multiplier": 0.85},
    "IN": {"currency": "inr", "symbol": "₹", "multiplier": 0.15},
}
DEFAULT_PRICING = {"currency": "usd", "symbol": "$", "multiplier": 0.7}

EXCHANGE_RATES = {
    "usd": 1.0, "eur": 0.92, "gbp": 0.79, "aud": 1.53, "cad": 1.36,
    "aed": 3.67, "inr": 83.1
}

def calculate_price(country_code: str) -> dict:
    pricing = COUNTRY_PRICING.get(country_code.upper(), DEFAULT_PRICING)
    currency = pricing["currency"]
    exchange_rate = EXCHANGE_RATES.get(currency, 1.0)
    base_amount = BASE_PRICE_USD_CENTS * pricing["multiplier"] * exchange_rate / 100
    final_amount = round(base_amount, 2)
    if final_amount < 0.5:
        final_amount = 0.50
    stripe_amount = int(final_amount * 100)
    display_price = f"{pricing['symbol']}{final_amount:.2f}"
    return {
        "amount": stripe_amount,
        "currency": currency,
        "display_price": display_price,
        "symbol": pricing["symbol"]
    }

app = FastAPI(title="Luleit PDF Editor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

analytics = {
    "total_uploads": 0,
    "total_downloads": 0,
    "total_edits": 0,
}
pdf_storage = {}

def extract_font_family(font_name):
    if not font_name: return "Arial, sans-serif"
    fl = font_name.lower()
    if "arial" in fl or "helvetica" in fl: return "Arial, sans-serif"
    elif "times" in fl: return "Times New Roman, serif"
    elif "courier" in fl: return "Courier New, monospace"
    return "Arial, sans-serif"

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html") as f:
        return f.read()

@app.post("/upload-pdf")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 50*1024*1024:
        raise HTTPException(400, "File too large (max 50MB)")
    
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except:
        raise HTTPException(400, "Invalid PDF file")
    
    analytics["total_uploads"] += 1
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content": content, "filename": file.filename, "created": datetime.now()}
    
    fonts_used, sizes_used, colors_used = {}, {}, {}
    pages_data = []
    scale = 1.8
    
    for pn in range(len(doc)):
        page = doc[pn]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        
        if Image:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='JPEG', quality=85, optimize=True)
            img_data = "data:image/jpeg;base64," + base64.b64encode(img_buffer.getvalue()).decode()
        else:
            img_data = "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()
        
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
                                    "x": span["bbox"][0] * scale, "y": span["bbox"][1] * scale,
                                    "width": (span["bbox"][2] - span["bbox"][0]) * scale,
                                    "height": (span["bbox"][3] - span["bbox"][1]) * scale,
                                    "fontSize": span["size"] * scale,
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
            "image": img_data,
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
    
    pdf_id = str(uuid.uuid4())
    output = io.BytesIO()
    merged.save(output)
    merged.close()
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content": content, "filename": "merged.pdf", "created": datetime.now()}
    
    # Generate preview
    doc = fitz.open(stream=content, filetype="pdf")
    pages = []
    for i in range(min(len(doc), 50)):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        pages.append({"pageNum": i+1, "width": pix.width, "height": pix.height, 
                      "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()})
    doc.close()
    
    return {
        "pdfId": pdf_id,
        "pageCount": len(pages),
        "pages": pages,
        "fileName": "merged.pdf",
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
    
    savings = ((original_size - len(compressed)) / original_size) * 100
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content": compressed, "filename": f"compressed_{file.filename}", "created": datetime.now()}
    
    return {
        "pdfId": pdf_id,
        "originalSize": f"{original_size/1024:.1f} KB",
        "compressedSize": f"{len(compressed)/1024:.1f} KB",
        "savings": f"{savings:.1f}%",
        "fileName": f"compressed_{file.filename}"
    }

@app.get("/download-direct/{pdf_id}")
async def download_direct(pdf_id: str):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    s = pdf_storage[pdf_id]
    return StreamingResponse(io.BytesIO(s["content"]), media_type="application/pdf", 
                            headers={"Content-Disposition": f"attachment; filename={s['filename']}"})

# ============ STRIPE ENDPOINTS ============
@app.get("/api/stripe-config")
async def get_stripe_config():
    pk = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    enabled = STRIPE_ENABLED and bool(pk)
    print(f"Stripe config requested - enabled: {enabled}, pk present: {bool(pk)}")
    return {"publishable_key": pk, "enabled": enabled}

@app.get("/api/pricing")
async def get_pricing(request: Request):
    country_code = (
        request.headers.get("cf-ipcountry") or 
        request.headers.get("x-vercel-ip-country") or
        "US"
    )
    pricing = calculate_price(country_code)
    return pricing

@app.post("/api/create-payment")
async def create_payment(
    request: Request, 
    pdf_id: str = Form(...),
    edit_count: int = Form(0),
    page_count: int = Form(1),
    time_spent_seconds: int = Form(0),
    interaction_count: int = Form(0),
    has_added_images: bool = Form(False),
    has_signature: bool = Form(False),
    has_drawings: bool = Form(False),
    device_type: str = Form("desktop"),
    os: str = Form("unknown"),
    browser: str = Form("unknown"),
    is_premium_device: bool = Form(False),
    can_apple_pay: bool = Form(False),
    can_google_pay: bool = Form(True)
):
    if not STRIPE_ENABLED:
        raise HTTPException(500, "Payments not configured - STRIPE_SECRET_KEY missing")
    
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    country_code = (
        request.headers.get("cf-ipcountry") or 
        request.headers.get("x-vercel-ip-country") or
        "US"
    )
    
    pricing = calculate_price(country_code)
    
    try:
        intent = stripe.PaymentIntent.create(
            amount=pricing["amount"],
            currency=pricing["currency"],
            metadata={"pdf_id": pdf_id, "country": country_code},
            automatic_payment_methods={"enabled": True}
        )
        
        return {
            "clientSecret": intent.client_secret,
            "paymentIntentId": intent.id,
            "amount": pricing["amount"],
            "currency": pricing["currency"],
            "display_price": pricing["display_price"]
        }
    except Exception as e:
        print(f"Stripe error: {e}")
        raise HTTPException(500, f"Payment error: {str(e)}")

@app.post("/api/verify-payment")
async def verify_payment(request: Request, payment_intent_id: str = Form(...)):
    if not STRIPE_ENABLED:
        raise HTTPException(500, "Payments not configured")
    
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        if intent.status == "succeeded":
            pdf_id = intent.metadata.get("pdf_id")
            return {
                "success": True,
                "pdf_id": pdf_id,
                "download_token": base64.b64encode(f"{pdf_id}:{payment_intent_id}".encode()).decode()
            }
        else:
            return {"success": False, "status": intent.status}
    except Exception as e:
        raise HTTPException(500, f"Verification error: {str(e)}")

@app.post("/save-and-download")
async def save_and_download(
    request: Request, 
    pdf_id: str = Form(...), 
    edits: str = Form("[]"),
    token: str = Form(None)
):
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    # Check if Stripe is enabled and token is required
    if STRIPE_ENABLED:
        if not token:
            raise HTTPException(403, "Payment required")
        try:
            decoded = base64.b64decode(token).decode()
            token_pdf_id, payment_id = decoded.split(":")
            if token_pdf_id != pdf_id:
                raise HTTPException(403, "Invalid token")
        except:
            raise HTTPException(403, "Invalid payment token")
    
    content = pdf_storage[pdf_id]["content"]
    edit_list = json.loads(edits) if edits else []
    
    if edit_list:
        try:
            doc = fitz.open(stream=content, filetype="pdf")
            for e in edit_list:
                try:
                    page_num = e.get("pageNum", 1) - 1
                    if page_num < 0 or page_num >= len(doc):
                        continue
                    page = doc[page_num]
                    
                    x = float(e.get("originalX", 0))
                    y = float(e.get("originalY", 0))
                    w = float(e.get("originalWidth", 100))
                    h = float(e.get("originalHeight", 20))
                    font_size = float(e.get("originalFontSize", 12))
                    
                    rect = fitz.Rect(x - 1, y - 1, x + w + 2, y + h + 2)
                    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
                    
                    new_text = e.get("newText", "")
                    if new_text:
                        text_y = y + h - 2
                        page.insert_text(fitz.Point(x, text_y), new_text, fontsize=font_size, color=(0, 0, 0))
                except Exception as edit_err:
                    print(f"Edit error: {edit_err}")
                    continue
            
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True, clean=True, linear=False)
            doc.close()
            content = output.getvalue()
            analytics["total_edits"] += len(edit_list)
        except Exception as save_err:
            print(f"PDF save error: {save_err}")
            content = pdf_storage[pdf_id]["content"]
    
    analytics["total_downloads"] += 1
    
    return StreamingResponse(
        io.BytesIO(content), 
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=luleit-edited.pdf",
            "Content-Type": "application/pdf",
            "Cache-Control": "no-cache"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
