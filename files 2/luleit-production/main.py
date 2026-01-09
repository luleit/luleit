from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import fitz
import os
import json
import tempfile
import base64
import stripe
import uuid
from datetime import datetime, timedelta

# ============ CONFIGURATION ============
# Set these environment variables in Railway/Render

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_xxx")  # Your Stripe secret key
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_xxx")  # Your Stripe publishable key
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")  # Webhook signing secret
DOMAIN = os.getenv("DOMAIN", "http://localhost:8000")  # Your domain
PRICE_AMOUNT = 100  # $1.00 in cents

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(title="Luleit PDF Editor")

# CORS for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, set to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Storage (In production, use Redis or database)
pdf_storage = {}
payment_sessions = {}

# ============ ROUTES ============

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r") as f:
        return f.read()

@app.get("/config")
def get_config():
    """Return publishable key to frontend"""
    return {"publishableKey": STRIPE_PUBLISHABLE_KEY}

@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    content = await file.read()
    
    # Validate file size (max 50MB)
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 50MB.")
    
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except:
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {
        "content": content,
        "filename": file.filename,
        "created": datetime.now(),
        "expires": datetime.now() + timedelta(hours=24)  # Auto-delete after 24h
    }
    
    pages_data = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(2.5, 2.5)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        
        text_blocks = []
        blocks = page.get_text("dict")["blocks"]
        
        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"].strip():
                            scale = 2.5
                            text_blocks.append({
                                "text": span["text"],
                                "x": span["bbox"][0] * scale,
                                "y": span["bbox"][1] * scale,
                                "width": (span["bbox"][2] - span["bbox"][0]) * scale,
                                "height": (span["bbox"][3] - span["bbox"][1]) * scale,
                                "fontSize": span["size"] * scale,
                                "fontName": span["font"],
                                "color": span["color"],
                                "originalX": span["bbox"][0],
                                "originalY": span["bbox"][1],
                                "originalWidth": span["bbox"][2] - span["bbox"][0],
                                "originalHeight": span["bbox"][3] - span["bbox"][1],
                                "originalFontSize": span["size"]
                            })
        
        pages_data.append({
            "pageNum": page_num + 1,
            "width": pix.width,
            "height": pix.height,
            "image": "data:image/png;base64," + img_base64,
            "textBlocks": text_blocks
        })
    
    doc.close()
    return JSONResponse({
        "pdfId": pdf_id,
        "pageCount": len(pages_data),
        "pages": pages_data,
        "fileName": file.filename
    })

@app.post("/create-checkout-session")
async def create_checkout_session(pdf_id: str = Form(...), edits: str = Form(...)):
    """Create Stripe Checkout Session"""
    if pdf_id not in pdf_storage:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    # Store edits temporarily
    session_id = str(uuid.uuid4())
    payment_sessions[session_id] = {
        "pdf_id": pdf_id,
        "edits": edits,
        "created": datetime.now()
    }
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'PDF Edit & Download',
                        'description': 'Download your edited PDF document',
                    },
                    'unit_amount': PRICE_AMOUNT,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{DOMAIN}/payment-success?session_id={session_id}",
            cancel_url=f"{DOMAIN}/payment-cancelled",
            metadata={
                'session_id': session_id
            }
        )
        return {"checkoutUrl": checkout_session.url, "sessionId": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/payment-success")
async def payment_success(session_id: str):
    """Handle successful payment - redirect to download"""
    if session_id not in payment_sessions:
        return HTMLResponse("<h1>Session expired</h1><p><a href='/'>Go back</a></p>")
    
    # Return HTML that auto-downloads
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Luleit</title>
        <style>
            body {{ font-family: 'Inter', sans-serif; background: #0f172a; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
            .container {{ text-align: center; padding: 40px; }}
            h1 {{ color: #10b981; margin-bottom: 16px; }}
            p {{ color: #94a3b8; margin-bottom: 24px; }}
            a {{ color: #6366f1; }}
            .spinner {{ width: 40px; height: 40px; border: 3px solid #334155; border-top-color: #6366f1; border-radius: 50%; animation: spin 1s linear infinite; margin: 20px auto; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✓ Payment Successful!</h1>
            <p>Your download will start automatically...</p>
            <div class="spinner"></div>
            <p><small>If download doesn't start, <a href="/download-pdf?session_id={session_id}">click here</a></small></p>
        </div>
        <script>
            setTimeout(() => {{
                window.location.href = '/download-pdf?session_id={session_id}';
            }}, 1500);
        </script>
    </body>
    </html>
    """)

@app.get("/payment-cancelled")
async def payment_cancelled():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Cancelled - Luleit</title>
        <style>
            body { font-family: 'Inter', sans-serif; background: #0f172a; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
            .container { text-align: center; padding: 40px; }
            h1 { color: #f59e0b; margin-bottom: 16px; }
            p { color: #94a3b8; margin-bottom: 24px; }
            a { background: #6366f1; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Payment Cancelled</h1>
            <p>No worries! Your edits are still saved.</p>
            <a href="/">← Back to Editor</a>
        </div>
    </body>
    </html>
    """)

@app.get("/download-pdf")
async def download_pdf(session_id: str):
    """Generate and download the edited PDF after payment"""
    if session_id not in payment_sessions:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    session_data = payment_sessions[session_id]
    pdf_id = session_data["pdf_id"]
    
    if pdf_id not in pdf_storage:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    content = pdf_storage[pdf_id]["content"]
    edit_data = json.loads(session_data["edits"])
    doc = fitz.open(stream=content, filetype="pdf")
    
    for edit in edit_data:
        page_num = edit["pageNum"] - 1
        page = doc[page_num]
        
        x = edit["originalX"]
        y = edit["originalY"]
        width = edit["originalWidth"]
        height = edit["originalHeight"]
        font_size = edit["originalFontSize"]
        
        rect = fitz.Rect(x - 1, y - 1, x + width + 5, y + height + 1)
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        if edit["newText"]:
            text_point = fitz.Point(x, y + height - 2)
            page.insert_text(
                text_point,
                edit["newText"],
                fontsize=font_size,
                color=(0, 0, 0),
                fontname="helv"
            )
    
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, "luleit-edited.pdf")
    doc.save(tmp_path)
    doc.close()
    
    # Clean up session after download
    del payment_sessions[session_id]
    
    return FileResponse(
        tmp_path,
        filename="luleit-edited.pdf",
        media_type="application/pdf"
    )

# Legacy endpoint for testing without payment
@app.post("/save-pdf")
async def save_pdf(pdf_id: str = Form(...), edits: str = Form(...)):
    if pdf_id not in pdf_storage:
        return JSONResponse({"error": "PDF not found"}, status_code=404)
    
    content = pdf_storage[pdf_id]["content"]
    edit_data = json.loads(edits)
    doc = fitz.open(stream=content, filetype="pdf")
    
    for edit in edit_data:
        page_num = edit["pageNum"] - 1
        page = doc[page_num]
        
        x = edit["originalX"]
        y = edit["originalY"]
        width = edit["originalWidth"]
        height = edit["originalHeight"]
        font_size = edit["originalFontSize"]
        
        rect = fitz.Rect(x - 1, y - 1, x + width + 5, y + height + 1)
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        if edit["newText"]:
            text_point = fitz.Point(x, y + height - 2)
            page.insert_text(
                text_point,
                edit["newText"],
                fontsize=font_size,
                color=(0, 0, 0),
                fontname="helv"
            )
    
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, "edited.pdf")
    doc.save(tmp_path)
    doc.close()
    
    return FileResponse(tmp_path, filename="edited.pdf", media_type="application/pdf")

@app.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks for payment confirmation"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # Payment successful - you could send email, log analytics, etc.
        print(f"Payment successful for session: {session.get('metadata', {}).get('session_id')}")
    
    return {"status": "success"}

# Cleanup old PDFs periodically (run this in a background task in production)
@app.on_event("startup")
async def cleanup_old_files():
    """In production, use a scheduled task to clean up expired files"""
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
