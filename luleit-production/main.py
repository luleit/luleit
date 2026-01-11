"""
Luleit PDF Editor - Professional Edition
Backend with 4-Layer Dynamic Pricing Intelligence
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import os
import uuid
import json
import stripe
import base64
from datetime import datetime, timedelta
from typing import Optional
import httpx

app = FastAPI(title="Luleit PDF Editor", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
UPLOAD_DIR = "/tmp/luleit_uploads"
OUTPUT_DIR = "/tmp/luleit_outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Stripe Configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")

# Token storage
download_tokens = {}

# ============================================================================
# 4-LAYER DYNAMIC PRICING INTELLIGENCE
# ============================================================================

# LAYER 1: Country Base Pricing (percentage of $2.99 USD base)
COUNTRY_PRICING = {
    # Tier 1: Premium Markets (100%+)
    "US": 100, "CH": 120, "NO": 115, "DK": 110, "SE": 105,
    "AU": 100, "NZ": 95, "GB": 95, "DE": 95, "NL": 95,
    "AT": 95, "BE": 95, "FI": 95, "IE": 95, "LU": 110,
    "SG": 100, "HK": 95, "JP": 90, "KR": 85,
    # Tier 2: Upper Middle (70-90%)
    "CA": 90, "FR": 85, "IT": 80, "ES": 75, "PT": 70,
    "AE": 85, "SA": 80, "QA": 90, "KW": 85, "BH": 80, "OM": 75,
    "IL": 80, "CY": 75, "MT": 75, "GR": 70,
    # Tier 3: Middle Markets (40-70%)
    "PL": 50, "CZ": 55, "HU": 50, "RO": 45, "BG": 40,
    "HR": 50, "SK": 50, "SI": 55, "EE": 55, "LV": 50, "LT": 50,
    "MY": 45, "TH": 40, "CN": 50, "TW": 65, "RU": 35,
    "MX": 40, "BR": 35, "AR": 30, "CL": 45, "CO": 35,
    "TR": 35, "ZA": 40, "EG": 30, "MA": 35, "NG": 25,
    # Tier 4: Emerging Markets (15-40%)
    "IN": 15, "PK": 12, "BD": 10, "LK": 20, "NP": 10,
    "ID": 20, "PH": 25, "VN": 20, "MM": 15, "KH": 15,
    "KE": 20, "GH": 20, "TZ": 15, "UG": 15, "ET": 10,
    "UA": 25, "BY": 25, "KZ": 30, "UZ": 20,
}

# LAYER 2: City/Neighborhood Premium Zones
CITY_PRICING = {
    # UAE - Detailed neighborhood pricing
    "AE": {
        "dubai": {
            "palm jumeirah": 70, "downtown dubai": 65, "dubai marina": 70,
            "jumeirah beach": 60, "emirates hills": 75, "arabian ranches": 55,
            "business bay": 50, "difc": 65, "city walk": 55, "bluewaters": 60,
            "dubai hills": 50, "al barsha": 30, "jlt": 35, "discovery gardens": 20,
            "deira": 35, "bur dubai": 30, "karama": 25, "al qusais": 20,
            "international city": 15, "dubai silicon oasis": 25,
            "_default": 40
        },
        "abu dhabi": {
            "saadiyat island": 65, "yas island": 55, "al reem island": 50,
            "corniche": 45, "al raha": 40, "khalifa city": 35,
            "_default": 35
        },
        "sharjah": {"_default": 25},
        "ajman": {"_default": 20},
        "ras al khaimah": {"_default": 20},
        "fujairah": {"_default": 15},
        "umm al quwain": {"_default": 15},
    },
    # Saudi Arabia
    "SA": {
        "riyadh": {
            "king abdullah financial district": 60, "diplomatic quarter": 55,
            "al olaya": 50, "al nakheel": 45, "al malqa": 40,
            "_default": 35
        },
        "jeddah": {
            "al rawdah": 45, "al hamra": 40, "al zahra": 35,
            "_default": 30
        },
        "dammam": {"_default": 25},
        "khobar": {"al khobar": 35, "_default": 30},
    },
    # USA - Major metros
    "US": {
        "new york": {
            "manhattan": 50, "tribeca": 60, "soho": 55, "upper east side": 50,
            "brooklyn heights": 40, "williamsburg": 35,
            "_default": 30
        },
        "san francisco": {
            "pacific heights": 55, "nob hill": 50, "marina": 45,
            "soma": 35, "financial district": 40,
            "_default": 30
        },
        "los angeles": {
            "beverly hills": 55, "bel air": 60, "santa monica": 45,
            "hollywood": 30, "malibu": 50,
            "_default": 25
        },
        "miami": {
            "miami beach": 45, "coral gables": 40, "brickell": 40,
            "key biscayne": 50,
            "_default": 25
        },
        "seattle": {"bellevue": 35, "mercer island": 40, "_default": 20},
        "boston": {"back bay": 40, "beacon hill": 45, "_default": 25},
        "chicago": {"gold coast": 40, "lincoln park": 35, "_default": 20},
    },
    # UK
    "GB": {
        "london": {
            "mayfair": 55, "chelsea": 50, "kensington": 50, "knightsbridge": 55,
            "hampstead": 45, "notting hill": 40, "canary wharf": 35,
            "_default": 25
        },
        "manchester": {"_default": 15},
        "birmingham": {"_default": 15},
    },
    # India - Tech hubs
    "IN": {
        "mumbai": {
            "south mumbai": 50, "bandra": 45, "juhu": 40, "powai": 35,
            "andheri": 25, "worli": 40,
            "_default": 20
        },
        "bangalore": {
            "koramangala": 70, "indiranagar": 65, "whitefield": 50,
            "hsr layout": 45, "jayanagar": 35, "electronic city": 30,
            "_default": 25
        },
        "delhi": {
            "defence colony": 45, "greater kailash": 40, "vasant kunj": 35,
            "south delhi": 35, "gurgaon": 50, "noida": 30,
            "_default": 20
        },
        "hyderabad": {
            "banjara hills": 45, "jubilee hills": 45, "hitec city": 40,
            "_default": 20
        },
        "pune": {"koregaon park": 35, "kalyani nagar": 30, "_default": 15},
        "chennai": {"adyar": 30, "anna nagar": 25, "_default": 15},
    },
    # Singapore
    "SG": {
        "singapore": {
            "orchard": 45, "marina bay": 50, "sentosa": 55, "holland village": 35,
            "bukit timah": 40, "river valley": 35,
            "_default": 25
        }
    },
    # Hong Kong
    "HK": {
        "hong kong": {
            "the peak": 60, "central": 50, "mid-levels": 45, "repulse bay": 50,
            "causeway bay": 35, "tsim sha tsui": 30,
            "_default": 25
        }
    },
    # Australia
    "AU": {
        "sydney": {
            "point piper": 55, "double bay": 50, "mosman": 45, "bondi": 40,
            "cbd": 35,
            "_default": 25
        },
        "melbourne": {
            "toorak": 50, "south yarra": 40, "brighton": 40,
            "_default": 20
        },
    },
    # Switzerland
    "CH": {
        "zurich": {"_default": 35},
        "geneva": {"_default": 35},
        "zug": {"_default": 40},
    },
    # Germany
    "DE": {
        "munich": {"bogenhausen": 35, "schwabing": 30, "_default": 20},
        "frankfurt": {"westend": 30, "_default": 20},
        "berlin": {"charlottenburg": 25, "mitte": 25, "_default": 15},
    },
}

# LAYER 3: Behavioral/Sunk Cost Pricing
def calculate_engagement_premium(edit_count: int, time_spent_seconds: int, features_used: list) -> int:
    """Calculate premium based on user engagement (sunk cost psychology)"""
    premium = 0
    
    # Edit count premium
    if edit_count >= 20:
        premium += 25
    elif edit_count >= 10:
        premium += 15
    elif edit_count >= 5:
        premium += 8
    elif edit_count >= 3:
        premium += 3
    
    # Time spent premium (minutes)
    minutes = time_spent_seconds / 60
    if minutes >= 30:
        premium += 25
    elif minutes >= 15:
        premium += 15
    elif minutes >= 10:
        premium += 10
    elif minutes >= 5:
        premium += 5
    
    # Feature usage premium
    premium_features = {
        "signature": 8,
        "image": 5,
        "merge": 10,
        "form_fill": 8,
        "redact": 12,
        "watermark": 5,
    }
    for feature in features_used:
        premium += premium_features.get(feature, 0)
    
    # Low engagement discount (encourage conversion)
    if edit_count <= 1 and minutes < 2:
        premium = -15  # Discount for quick users
    
    return premium

# LAYER 4: Device/Platform Pricing
def calculate_device_premium(user_agent: str) -> int:
    """Calculate premium based on device/OS (Apple users pay more)"""
    ua_lower = user_agent.lower()
    
    # Apple ecosystem premium
    if "iphone" in ua_lower or "ipad" in ua_lower:
        return 12
    if "macintosh" in ua_lower or "mac os" in ua_lower:
        return 10
    
    # Safari browser (often indicates Apple)
    if "safari" in ua_lower and "chrome" not in ua_lower:
        return 5
    
    # High-end Android (Samsung flagship, etc.)
    if "sm-g" in ua_lower or "sm-n" in ua_lower or "sm-f" in ua_lower:
        return 5
    
    # Windows/Linux (standard)
    if "windows nt 10" in ua_lower or "windows nt 11" in ua_lower:
        return 2
    
    # Old devices (discount)
    if "android 7" in ua_lower or "android 6" in ua_lower or "windows nt 6" in ua_lower:
        return -5
    
    return 0

# Master pricing function
async def calculate_dynamic_price(
    request: Request,
    edit_count: int = 0,
    time_spent_seconds: int = 0,
    features_used: list = None
) -> dict:
    """Calculate final price using all 4 layers"""
    
    features_used = features_used or []
    base_price_usd = 2.99
    
    # Get user info
    user_agent = request.headers.get("user-agent", "")
    
    # Try to get location from IP
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    
    country_code = "US"
    city = ""
    region = ""
    
    # Geo lookup (with fallback)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            geo_response = await client.get(f"http://ip-api.com/json/{client_ip}")
            if geo_response.status_code == 200:
                geo_data = geo_response.json()
                country_code = geo_data.get("countryCode", "US")
                city = geo_data.get("city", "").lower()
                region = geo_data.get("regionName", "").lower()
    except:
        pass
    
    # LAYER 1: Country base
    country_multiplier = COUNTRY_PRICING.get(country_code, 70) / 100
    
    # LAYER 2: City/Neighborhood premium
    city_premium = 0
    if country_code in CITY_PRICING:
        country_cities = CITY_PRICING[country_code]
        for city_name, areas in country_cities.items():
            if city_name in city.lower() or city_name in region.lower():
                if isinstance(areas, dict):
                    # Check specific neighborhoods
                    matched = False
                    for area, premium in areas.items():
                        if area != "_default" and area in city.lower():
                            city_premium = premium
                            matched = True
                            break
                    if not matched:
                        city_premium = areas.get("_default", 0)
                else:
                    city_premium = areas
                break
    
    # LAYER 3: Engagement/Sunk Cost premium
    engagement_premium = calculate_engagement_premium(edit_count, time_spent_seconds, features_used)
    
    # LAYER 4: Device premium
    device_premium = calculate_device_premium(user_agent)
    
    # Calculate final price
    total_premium_percent = city_premium + engagement_premium + device_premium
    final_multiplier = country_multiplier * (1 + total_premium_percent / 100)
    
    # Apply floor and ceiling
    raw_price = base_price_usd * final_multiplier
    final_price = max(0.25, min(5.99, raw_price))  # $0.25 floor, $5.99 ceiling
    final_price = round(final_price, 2)
    
    return {
        "price": final_price,
        "currency": "usd",
        "country": country_code,
        "city": city,
        "breakdown": {
            "base_price": base_price_usd,
            "country_multiplier": country_multiplier,
            "city_premium": city_premium,
            "engagement_premium": engagement_premium,
            "device_premium": device_premium,
            "final_multiplier": round(final_multiplier, 3),
        }
    }


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the landing page"""
    with open("templates/index.html", "r") as f:
        return f.read()

@app.get("/edit", response_class=HTMLResponse)
@app.get("/editor", response_class=HTMLResponse)
async def editor():
    """Serve the editor page"""
    with open("templates/editor.html", "r") as f:
        return f.read()

@app.get("/merge", response_class=HTMLResponse)
async def merge_page():
    """Serve merge PDF page"""
    with open("templates/merge.html", "r") as f:
        return f.read()

@app.get("/compress", response_class=HTMLResponse)
async def compress_page():
    """Serve compress PDF page"""
    with open("templates/compress.html", "r") as f:
        return f.read()

@app.get("/sign", response_class=HTMLResponse)
async def sign_page():
    """Serve sign PDF page"""
    with open("templates/sign.html", "r") as f:
        return f.read()

@app.get("/api/health")
async def health():
    return {"status": "healthy", "version": "2.0.0"}

@app.get("/api/config")
async def get_config():
    """Return public configuration"""
    return {
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "stripe_enabled": bool(STRIPE_PUBLISHABLE_KEY and stripe.api_key),
    }

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload and process PDF"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Only PDF files are allowed")
    
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(400, "File too large (max 50MB)")
    
    with open(file_path, "wb") as f:
        f.write(content)
    
    try:
        doc = fitz.open(file_path)
        pages = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            img_data = base64.b64encode(pix.tobytes("png")).decode()
            
            # Extract text blocks
            text_blocks = []
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text_blocks.append({
                                "text": span.get("text", ""),
                                "x": span.get("bbox", [0])[0],
                                "y": span.get("bbox", [0, 0])[1],
                                "width": span.get("bbox", [0, 0, 0])[2] - span.get("bbox", [0])[0],
                                "height": span.get("bbox", [0, 0, 0, 0])[3] - span.get("bbox", [0, 0])[1],
                                "fontSize": span.get("size", 12),
                                "fontFamily": span.get("font", "Helvetica"),
                                "color": "#{:06x}".format(span.get("color", 0)),
                            })
            
            pages.append({
                "pageNumber": i + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "image": f"data:image/png;base64,{img_data}",
                "textBlocks": text_blocks,
            })
        
        doc.close()
        
        return {
            "fileId": file_id,
            "fileName": file.filename,
            "pageCount": len(pages),
            "pages": pages,
        }
    except Exception as e:
        raise HTTPException(500, f"Error processing PDF: {str(e)}")

@app.post("/api/save")
async def save_pdf(
    request: Request,
    file_id: str = Form(...),
    edits: str = Form(...),
):
    """Save edited PDF"""
    file_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    
    try:
        edits_data = json.loads(edits)
        doc = fitz.open(file_path)
        
        for edit in edits_data:
            page_num = edit.get("page", 1) - 1
            if page_num < 0 or page_num >= len(doc):
                continue
            
            page = doc[page_num]
            edit_type = edit.get("type")
            
            if edit_type == "text":
                x, y = edit.get("x", 0), edit.get("y", 0)
                text = edit.get("text", "")
                font_size = edit.get("fontSize", 12)
                color_hex = edit.get("color", "#000000").lstrip("#")
                color = tuple(int(color_hex[i:i+2], 16) / 255 for i in (0, 2, 4))
                
                page.insert_text(
                    (x, y + font_size),
                    text,
                    fontsize=font_size,
                    color=color,
                )
            
            elif edit_type == "image":
                img_data = edit.get("data", "")
                if img_data.startswith("data:"):
                    img_data = img_data.split(",")[1]
                img_bytes = base64.b64decode(img_data)
                
                rect = fitz.Rect(
                    edit.get("x", 0),
                    edit.get("y", 0),
                    edit.get("x", 0) + edit.get("width", 100),
                    edit.get("y", 0) + edit.get("height", 100),
                )
                page.insert_image(rect, stream=img_bytes)
            
            elif edit_type == "draw":
                paths = edit.get("paths", [])
                color_hex = edit.get("color", "#000000").lstrip("#")
                color = tuple(int(color_hex[i:i+2], 16) / 255 for i in (0, 2, 4))
                width = edit.get("width", 2)
                
                for path in paths:
                    if len(path) >= 2:
                        shape = page.new_shape()
                        shape.draw_polyline([(p["x"], p["y"]) for p in path])
                        shape.finish(color=color, width=width)
                        shape.commit()
            
            elif edit_type == "highlight":
                rect = fitz.Rect(
                    edit.get("x", 0),
                    edit.get("y", 0),
                    edit.get("x", 0) + edit.get("width", 100),
                    edit.get("y", 0) + edit.get("height", 20),
                )
                highlight = page.add_highlight_annot(rect)
                highlight.update()
        
        output_path = os.path.join(OUTPUT_DIR, f"{file_id}_edited.pdf")
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        
        return {"fileId": file_id, "status": "saved"}
    
    except Exception as e:
        raise HTTPException(500, f"Error saving PDF: {str(e)}")

@app.post("/api/merge")
async def merge_pdfs(files: list[UploadFile] = File(...)):
    """Merge multiple PDFs"""
    if len(files) < 2:
        raise HTTPException(400, "Need at least 2 files to merge")
    
    merged = fitz.open()
    
    try:
        for file in files:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            merged.insert_pdf(doc)
            doc.close()
        
        output_id = str(uuid.uuid4())
        output_path = os.path.join(OUTPUT_DIR, f"{output_id}_merged.pdf")
        merged.save(output_path, garbage=4, deflate=True)
        merged.close()
        
        return {"fileId": output_id, "fileName": "merged.pdf"}
    
    except Exception as e:
        raise HTTPException(500, f"Error merging PDFs: {str(e)}")

@app.post("/api/compress")
async def compress_pdf(file: UploadFile = File(...)):
    """Compress PDF"""
    content = await file.read()
    original_size = len(content)
    
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        
        output_id = str(uuid.uuid4())
        output_path = os.path.join(OUTPUT_DIR, f"{output_id}_compressed.pdf")
        
        doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
            linear=True,
        )
        doc.close()
        
        compressed_size = os.path.getsize(output_path)
        
        return {
            "fileId": output_id,
            "originalSize": original_size,
            "compressedSize": compressed_size,
            "reduction": round((1 - compressed_size / original_size) * 100, 1),
        }
    
    except Exception as e:
        raise HTTPException(500, f"Error compressing PDF: {str(e)}")

@app.post("/api/price")
async def get_price(
    request: Request,
    edit_count: int = Form(0),
    time_spent: int = Form(0),
    features: str = Form("[]"),
):
    """Get dynamic price based on all 4 layers"""
    features_list = json.loads(features) if features else []
    pricing = await calculate_dynamic_price(request, edit_count, time_spent, features_list)
    return pricing

@app.post("/api/create-payment")
async def create_payment(
    request: Request,
    file_id: str = Form(...),
    edit_count: int = Form(0),
    time_spent: int = Form(0),
    features: str = Form("[]"),
):
    """Create Stripe payment intent"""
    if not stripe.api_key:
        raise HTTPException(400, "Payments not configured")
    
    features_list = json.loads(features) if features else []
    pricing = await calculate_dynamic_price(request, edit_count, time_spent, features_list)
    
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(pricing["price"] * 100),
            currency="usd",
            metadata={
                "file_id": file_id,
                "country": pricing["country"],
                "city": pricing.get("city", ""),
            },
        )
        
        return {
            "clientSecret": intent.client_secret,
            "price": pricing["price"],
            "breakdown": pricing["breakdown"],
        }
    
    except Exception as e:
        raise HTTPException(500, f"Payment error: {str(e)}")

@app.post("/api/verify-payment")
async def verify_payment(payment_intent_id: str = Form(...), file_id: str = Form(...)):
    """Verify payment and generate download token"""
    if not stripe.api_key:
        # Free mode - generate token directly
        token = str(uuid.uuid4())
        download_tokens[token] = {
            "file_id": file_id,
            "expires": datetime.now() + timedelta(hours=24),
        }
        return {"token": token, "status": "success"}
    
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        if intent.status == "succeeded":
            token = str(uuid.uuid4())
            download_tokens[token] = {
                "file_id": file_id,
                "expires": datetime.now() + timedelta(hours=24),
            }
            return {"token": token, "status": "success"}
        else:
            raise HTTPException(400, "Payment not completed")
    
    except Exception as e:
        raise HTTPException(500, f"Verification error: {str(e)}")

@app.get("/api/download/{token}")
async def download_pdf(token: str):
    """Download edited PDF with valid token"""
    if token not in download_tokens:
        raise HTTPException(403, "Invalid or expired token")
    
    token_data = download_tokens[token]
    if datetime.now() > token_data["expires"]:
        del download_tokens[token]
        raise HTTPException(403, "Token expired")
    
    file_id = token_data["file_id"]
    file_path = os.path.join(OUTPUT_DIR, f"{file_id}_edited.pdf")
    
    if not os.path.exists(file_path):
        # Try other output patterns
        for pattern in ["_merged.pdf", "_compressed.pdf"]:
            alt_path = os.path.join(OUTPUT_DIR, f"{file_id}{pattern}")
            if os.path.exists(alt_path):
                file_path = alt_path
                break
    
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename="luleit_edited.pdf",
    )

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
