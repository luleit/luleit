"""
Luleit AI - India-Focused PDF Editor with Self-Learning Pricing
================================================================

INDIA-SPECIFIC PRICING INTELLIGENCE:

Geographic Signals:
- Metro cities: Mumbai, Delhi, Bangalore, Chennai, Hyderabad, Kolkata, Pune
- Tier 2: Ahmedabad, Jaipur, Lucknow, Chandigarh, Indore, Coimbatore, etc.
- Tier 3: Smaller cities, towns
- State GDP variations (Maharashtra vs Bihar)

Device Signals (India-specific):
- iPhone/iPad = High WTP (top 1%)
- OnePlus/Samsung flagship = Upper middle class
- Samsung mid-range = Middle class  
- Xiaomi/Redmi/Realme/Poco = Price sensitive
- Vivo/Oppo = Varies by model
- Jio Phone = Very price sensitive

Behavioral Signals:
- Salary week (1st-7th of month) = Higher WTP
- Month end (25th-31st) = Lower WTP
- Festival seasons = Higher spending
- Late night usage = Often professionals
- Business documents = Higher WTP

Payment Signals:
- UPI preference = Price conscious
- Card ready = Higher WTP
- Multiple payment attempts = Very interested

The AI learns from:
- Conversions: What price + signals = payment
- Bounces: What price was too high for what signals
- Time on pricing page: Hesitation analysis
"""

import os
import io
import json
import uuid
import base64
import hashlib
import asyncio
import httpx
import fitz
import zipfile
import re
import subprocess
import tempfile
import shutil
from PIL import Image
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ============================================================================
# APP CONFIG
# ============================================================================

app = FastAPI(title="Luleit AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "outputs"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Adobe PDF Services API credentials (set via environment variables)
ADOBE_CLIENT_ID = os.getenv("ADOBE_CLIENT_ID", "")
ADOBE_CLIENT_SECRET = os.getenv("ADOBE_CLIENT_SECRET", "")

# ============================================================================
# ADOBE PDF SERVICES
# ============================================================================

class AdobePDFServices:
    """Adobe PDF Services API client for PDF <-> Word conversion"""

    TOKEN_URL = "https://pdf-services.adobe.io/token"
    API_BASE = "https://pdf-services.adobe.io"

    _access_token: Optional[str] = None
    _token_expires: Optional[datetime] = None

    @classmethod
    async def get_access_token(cls) -> str:
        """Get or refresh Adobe access token"""
        # Return cached token if still valid
        if cls._access_token and cls._token_expires and datetime.now() < cls._token_expires:
            return cls._access_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                cls.TOKEN_URL,
                data={
                    "client_id": ADOBE_CLIENT_ID,
                    "client_secret": ADOBE_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code != 200:
                print(f"Adobe token error: {response.status_code} - {response.text}")
                raise HTTPException(500, "Failed to authenticate with Adobe")

            data = response.json()
            cls._access_token = data["access_token"]
            # Token expires in ~24 hours, refresh after 23 hours
            cls._token_expires = datetime.now() + timedelta(hours=23)
            return cls._access_token

    @classmethod
    async def upload_asset(cls, file_bytes: bytes, media_type: str = "application/pdf") -> tuple:
        """Upload file to Adobe and get asset ID and upload URI"""
        token = await cls.get_access_token()

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Get upload URI
            response = await client.post(
                f"{cls.API_BASE}/assets",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": ADOBE_CLIENT_ID,
                    "Content-Type": "application/json",
                },
                json={"mediaType": media_type}
            )

            if response.status_code != 200:
                print(f"Adobe upload URI error: {response.status_code} - {response.text}")
                raise HTTPException(500, "Failed to get Adobe upload URI")

            data = response.json()
            upload_uri = data["uploadUri"]
            asset_id = data["assetID"]

            # Step 2: Upload the file
            response = await client.put(
                upload_uri,
                content=file_bytes,
                headers={"Content-Type": media_type}
            )

            if response.status_code not in [200, 201]:
                print(f"Adobe upload error: {response.status_code}")
                raise HTTPException(500, "Failed to upload to Adobe")

            return asset_id, upload_uri

    @classmethod
    async def export_pdf_to_word(cls, pdf_bytes: bytes) -> bytes:
        """Convert PDF to Word document"""
        token = await cls.get_access_token()
        asset_id, _ = await cls.upload_asset(pdf_bytes, "application/pdf")

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Create export job
            response = await client.post(
                f"{cls.API_BASE}/operation/exportpdf",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": ADOBE_CLIENT_ID,
                    "Content-Type": "application/json",
                },
                json={
                    "assetID": asset_id,
                    "targetFormat": "docx",
                }
            )

            if response.status_code not in [200, 201, 202]:
                print(f"Adobe export error: {response.status_code} - {response.text}")
                raise HTTPException(500, "Failed to start PDF export")

            # Get polling location
            poll_url = response.headers.get("location") or response.headers.get("x-request-id")
            if not poll_url:
                # Response might contain the result directly
                data = response.json()
                if "asset" in data:
                    download_uri = data["asset"]["downloadUri"]
                    result = await client.get(download_uri)
                    return result.content

            # Poll for completion
            for _ in range(60):  # Max 60 attempts (2 minutes)
                await asyncio.sleep(2)

                status_response = await client.get(
                    poll_url if poll_url.startswith("http") else f"{cls.API_BASE}{poll_url}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-api-key": ADOBE_CLIENT_ID,
                    }
                )

                if status_response.status_code == 200:
                    data = status_response.json()
                    status = data.get("status", "")

                    if status == "done":
                        download_uri = data.get("asset", {}).get("downloadUri") or data.get("downloadUri")
                        if download_uri:
                            result = await client.get(download_uri)
                            return result.content
                    elif status == "failed":
                        raise HTTPException(500, "Adobe PDF export failed")
                elif status_response.status_code == 202:
                    continue  # Still processing

            raise HTTPException(500, "Adobe PDF export timed out")

    @classmethod
    async def create_pdf_from_word(cls, docx_bytes: bytes) -> bytes:
        """Convert Word document to PDF"""
        token = await cls.get_access_token()
        asset_id, _ = await cls.upload_asset(docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Create PDF job
            response = await client.post(
                f"{cls.API_BASE}/operation/createpdf",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": ADOBE_CLIENT_ID,
                    "Content-Type": "application/json",
                },
                json={"assetID": asset_id}
            )

            if response.status_code not in [200, 201, 202]:
                print(f"Adobe create PDF error: {response.status_code} - {response.text}")
                raise HTTPException(500, "Failed to start PDF creation")

            # Get polling location
            poll_url = response.headers.get("location")

            # Poll for completion
            for _ in range(60):
                await asyncio.sleep(2)

                status_response = await client.get(
                    poll_url if poll_url.startswith("http") else f"{cls.API_BASE}{poll_url}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-api-key": ADOBE_CLIENT_ID,
                    }
                )

                if status_response.status_code == 200:
                    data = status_response.json()
                    status = data.get("status", "")

                    if status == "done":
                        download_uri = data.get("asset", {}).get("downloadUri") or data.get("downloadUri")
                        if download_uri:
                            result = await client.get(download_uri)
                            return result.content
                    elif status == "failed":
                        raise HTTPException(500, "Adobe PDF creation failed")
                elif status_response.status_code == 202:
                    continue

            raise HTTPException(500, "Adobe PDF creation timed out")


# ============================================================================
# LIBREOFFICE CONVERSION
# ============================================================================

class LibreOfficeConverter:
    """Convert PDFs using LibreOffice headless mode"""

    @staticmethod
    def find_libreoffice() -> str:
        """Find the LibreOffice executable"""
        # Common paths
        paths = [
            "libreoffice",  # System path (Nix/Railway)
            "soffice",
            "/usr/bin/libreoffice",
            "/usr/bin/soffice",
            "/opt/libreoffice/program/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]

        for path in paths:
            if shutil.which(path):
                return path

        # Try to find it
        result = shutil.which("libreoffice") or shutil.which("soffice")
        if result:
            return result

        raise RuntimeError("LibreOffice not found. Please install LibreOffice.")

    @classmethod
    def pdf_to_docx(cls, pdf_bytes: bytes) -> bytes:
        """Convert PDF to DOCX using LibreOffice"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write PDF to temp file
            pdf_path = os.path.join(tmpdir, "input.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

            # Run LibreOffice conversion
            libreoffice = cls.find_libreoffice()

            try:
                result = subprocess.run(
                    [
                        libreoffice,
                        "--headless",
                        "--infilter=writer_pdf_import",
                        "--convert-to", "docx",
                        "--outdir", tmpdir,
                        pdf_path
                    ],
                    capture_output=True,
                    timeout=120,  # 2 minute timeout
                    text=True
                )

                if result.returncode != 0:
                    print(f"LibreOffice PDF->DOCX error: {result.stderr}")
                    raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

            except subprocess.TimeoutExpired:
                raise RuntimeError("LibreOffice conversion timed out")

            # Read the output DOCX
            docx_path = os.path.join(tmpdir, "input.docx")
            if not os.path.exists(docx_path):
                # Try alternative names
                for f in os.listdir(tmpdir):
                    if f.endswith(".docx"):
                        docx_path = os.path.join(tmpdir, f)
                        break
                else:
                    raise RuntimeError("DOCX output not found after conversion")

            with open(docx_path, "rb") as f:
                return f.read()

    @classmethod
    def docx_to_pdf(cls, docx_bytes: bytes) -> bytes:
        """Convert DOCX to PDF using LibreOffice"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write DOCX to temp file
            docx_path = os.path.join(tmpdir, "input.docx")
            with open(docx_path, "wb") as f:
                f.write(docx_bytes)

            # Run LibreOffice conversion
            libreoffice = cls.find_libreoffice()

            try:
                result = subprocess.run(
                    [
                        libreoffice,
                        "--headless",
                        "--convert-to", "pdf",
                        "--outdir", tmpdir,
                        docx_path
                    ],
                    capture_output=True,
                    timeout=120,
                    text=True
                )

                if result.returncode != 0:
                    print(f"LibreOffice DOCX->PDF error: {result.stderr}")
                    raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

            except subprocess.TimeoutExpired:
                raise RuntimeError("LibreOffice conversion timed out")

            # Read the output PDF
            pdf_path = os.path.join(tmpdir, "input.pdf")
            if not os.path.exists(pdf_path):
                for f in os.listdir(tmpdir):
                    if f.endswith(".pdf"):
                        pdf_path = os.path.join(tmpdir, f)
                        break
                else:
                    raise RuntimeError("PDF output not found after conversion")

            with open(pdf_path, "rb") as f:
                return f.read()

    @classmethod
    def html_to_docx(cls, html_content: str) -> bytes:
        """Convert HTML to DOCX using LibreOffice"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write HTML to temp file with proper structure
            html_path = os.path.join(tmpdir, "input.html")
            full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; font-size: 12pt; line-height: 1.5; }}
        p {{ margin: 0 0 10pt 0; }}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(full_html)

            # Run LibreOffice conversion
            libreoffice = cls.find_libreoffice()

            try:
                result = subprocess.run(
                    [
                        libreoffice,
                        "--headless",
                        "--convert-to", "docx",
                        "--outdir", tmpdir,
                        html_path
                    ],
                    capture_output=True,
                    timeout=60,
                    text=True
                )

                if result.returncode != 0:
                    print(f"LibreOffice HTML->DOCX error: {result.stderr}")
                    raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

            except subprocess.TimeoutExpired:
                raise RuntimeError("LibreOffice conversion timed out")

            # Read the output DOCX
            docx_path = os.path.join(tmpdir, "input.docx")
            if not os.path.exists(docx_path):
                for f in os.listdir(tmpdir):
                    if f.endswith(".docx"):
                        docx_path = os.path.join(tmpdir, f)
                        break
                else:
                    raise RuntimeError("DOCX output not found after conversion")

            with open(docx_path, "rb") as f:
                return f.read()


def docx_to_html(docx_bytes: bytes) -> str:
    """Convert DOCX to editable HTML"""
    html_parts = ['<div class="docx-content">']

    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            # Read the main document
            if 'word/document.xml' in zf.namelist():
                doc_xml = zf.read('word/document.xml').decode('utf-8')

                # Simple XML to HTML conversion
                # Remove namespaces for easier parsing
                doc_xml = re.sub(r'<w:', '<', doc_xml)
                doc_xml = re.sub(r'</w:', '</', doc_xml)
                doc_xml = re.sub(r'\sw:\w+="[^"]*"', '', doc_xml)

                # Extract paragraphs
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', doc_xml, re.DOTALL)

                for para in paragraphs:
                    # Extract text runs
                    text_content = ""
                    runs = re.findall(r'<r[^>]*>(.*?)</r>', para, re.DOTALL)

                    for run in runs:
                        # Check for bold
                        is_bold = '<b/>' in run or '<b ' in run
                        # Check for italic
                        is_italic = '<i/>' in run or '<i ' in run
                        # Extract text
                        texts = re.findall(r'<t[^>]*>([^<]*)</t>', run)
                        text = ''.join(texts)

                        if text:
                            if is_bold:
                                text = f'<strong>{text}</strong>'
                            if is_italic:
                                text = f'<em>{text}</em>'
                            text_content += text

                    if text_content.strip():
                        html_parts.append(f'<p>{text_content}</p>')
                    else:
                        html_parts.append('<p><br></p>')

    except Exception as e:
        print(f"DOCX parse error: {e}")
        # Fallback: return placeholder
        html_parts.append('<p>Document content could not be parsed. Please use AI chat to make edits.</p>')

    html_parts.append('</div>')
    return '\n'.join(html_parts)


def html_to_docx(html_content: str, original_docx_bytes: bytes) -> bytes:
    """
    Update DOCX with edited HTML content.
    For now, we'll use a simple approach - recreate the document.xml
    """
    try:
        # Parse HTML to extract paragraphs
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html_content, re.DOTALL | re.IGNORECASE)

        # Build new document.xml content
        doc_xml_parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
            '<w:body>'
        ]

        for para in paragraphs:
            # Clean HTML tags and convert to Word XML
            para_xml = '<w:p><w:r>'

            # Handle bold
            if '<strong>' in para or '<b>' in para:
                para_xml = '<w:p><w:r><w:rPr><w:b/></w:rPr>'

            # Extract plain text
            plain_text = re.sub(r'<[^>]+>', '', para)
            plain_text = plain_text.replace('&nbsp;', ' ')
            plain_text = plain_text.replace('&amp;', '&')
            plain_text = plain_text.replace('&lt;', '<')
            plain_text = plain_text.replace('&gt;', '>')

            if plain_text.strip():
                para_xml += f'<w:t xml:space="preserve">{plain_text}</w:t>'
            else:
                para_xml += '<w:t></w:t>'

            para_xml += '</w:r></w:p>'
            doc_xml_parts.append(para_xml)

        doc_xml_parts.append('</w:body></w:document>')
        new_doc_xml = '\n'.join(doc_xml_parts)

        # Update the DOCX (ZIP) file
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original_docx_bytes), 'r') as zf_in:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf_out:
                for item in zf_in.namelist():
                    if item == 'word/document.xml':
                        zf_out.writestr(item, new_doc_xml.encode('utf-8'))
                    else:
                        zf_out.writestr(item, zf_in.read(item))

        return output.getvalue()

    except Exception as e:
        print(f"HTML to DOCX error: {e}")
        return original_docx_bytes


# ============================================================================
# STORAGE
# ============================================================================

documents: Dict[str, Dict] = {}
sessions: Dict[str, Dict] = {}
pricing_outcomes: List[Dict] = []

PRICING_FILE = DATA_DIR / "pricing_outcomes.json"

def load_pricing_data():
    global pricing_outcomes
    if PRICING_FILE.exists():
        try:
            with open(PRICING_FILE) as f:
                pricing_outcomes = json.load(f)
                print(f"Loaded {len(pricing_outcomes)} pricing records")
        except:
            pricing_outcomes = []

def save_pricing_data():
    try:
        with open(PRICING_FILE, "w") as f:
            json.dump(pricing_outcomes[-100000:], f)
    except Exception as e:
        print(f"Save error: {e}")

load_pricing_data()


# ============================================================================
# INDIA PRICING BRAIN
# ============================================================================

class IndiaPricingBrain:
    """
    India-specific pricing AI that learns optimal pricing.
    
    Price Range: ₹29 - ₹299 (roughly $0.35 - $3.60)
    Target: Maximize revenue while maintaining >15% conversion
    """
    
    # Price bounds in INR
    MIN_PRICE_INR = 29
    MAX_PRICE_INR = 299
    DEFAULT_PRICE_INR = 79
    
    # ==================== INDIA GEOGRAPHIC DATA ====================
    
    # Metro cities - highest WTP
    METRO_CITIES = {
        "mumbai", "delhi", "bangalore", "bengaluru", "chennai", 
        "hyderabad", "kolkata", "pune", "gurgaon", "gurugram",
        "noida", "navi mumbai", "thane", "ghaziabad", "faridabad"
    }
    
    # Tier 2 cities - medium WTP
    TIER2_CITIES = {
        "ahmedabad", "jaipur", "lucknow", "chandigarh", "indore",
        "coimbatore", "kochi", "cochin", "visakhapatnam", "vizag",
        "nagpur", "patna", "vadodara", "bhopal", "ludhiana",
        "agra", "nashik", "surat", "rajkot", "varanasi",
        "madurai", "mysore", "mysuru", "mangalore", "mangaluru",
        "trivandrum", "thiruvananthapuram", "bhubaneswar", "dehradun",
        "amritsar", "ranchi", "raipur", "guwahati", "vijayawada"
    }
    
    # State GDP multipliers (Maharashtra = 1.0 baseline)
    STATE_MULTIPLIERS = {
        # High GDP states
        "maharashtra": 1.0,
        "karnataka": 0.95,
        "tamil nadu": 0.9,
        "telangana": 0.95,
        "gujarat": 0.85,
        "delhi": 1.1,
        "haryana": 0.9,
        "kerala": 0.8,
        "andhra pradesh": 0.75,
        "punjab": 0.75,
        "west bengal": 0.65,
        
        # Medium GDP states
        "rajasthan": 0.6,
        "madhya pradesh": 0.55,
        "uttar pradesh": 0.5,
        "odisha": 0.55,
        "chhattisgarh": 0.55,
        "jharkhand": 0.5,
        "uttarakhand": 0.65,
        "himachal pradesh": 0.7,
        "goa": 0.9,
        "chandigarh": 0.95,
        
        # Lower GDP states
        "bihar": 0.35,
        "assam": 0.45,
        "tripura": 0.4,
        "meghalaya": 0.45,
        "manipur": 0.4,
        "mizoram": 0.45,
        "nagaland": 0.45,
        "arunachal pradesh": 0.45,
        "sikkim": 0.55,
        "jammu and kashmir": 0.5,
    }
    
    # ==================== DEVICE INTELLIGENCE ====================
    
    # Device brand WTP scores (0.0 to 1.0)
    DEVICE_SCORES = {
        # Premium (score: 0.9-1.0)
        "iphone": 1.0,
        "ipad": 1.0,
        "apple": 1.0,
        "macintosh": 0.95,
        
        # Upper-mid (score: 0.7-0.85)
        "oneplus": 0.85,
        "samsung galaxy s": 0.85,
        "samsung galaxy note": 0.85,
        "samsung galaxy z": 0.9,
        "pixel": 0.8,
        "google pixel": 0.8,
        "nothing phone": 0.75,
        "asus rog": 0.8,
        
        # Mid-range (score: 0.5-0.7)
        "samsung galaxy a": 0.6,
        "samsung galaxy m": 0.55,
        "samsung": 0.55,
        "motorola": 0.55,
        "nokia": 0.5,
        "iqoo": 0.6,
        
        # Budget (score: 0.3-0.5)
        "xiaomi": 0.4,
        "redmi": 0.35,
        "poco": 0.4,
        "realme": 0.35,
        "oppo": 0.45,
        "vivo": 0.45,
        "infinix": 0.3,
        "tecno": 0.25,
        "lava": 0.25,
        "micromax": 0.25,
        
        # Very budget (score: 0.1-0.25)
        "jio": 0.15,
        "jiophone": 0.1,
        "karbonn": 0.2,
        "intex": 0.2,
    }
    
    # ==================== TIME INTELLIGENCE ====================
    
    # Indian festivals (higher spending periods)
    FESTIVALS_2025 = [
        ("2025-01-14", "2025-01-15", "makar_sankranti", 1.1),
        ("2025-01-26", "2025-01-26", "republic_day", 1.05),
        ("2025-03-14", "2025-03-14", "holi", 1.15),
        ("2025-04-14", "2025-04-14", "tamil_new_year", 1.1),
        ("2025-08-15", "2025-08-15", "independence_day", 1.05),
        ("2025-08-27", "2025-08-27", "janmashtami", 1.05),
        ("2025-10-01", "2025-10-05", "durga_puja", 1.15),
        ("2025-10-20", "2025-10-24", "diwali", 1.25),  # Highest spending
        ("2025-11-01", "2025-11-01", "kannada_rajyotsava", 1.05),
        ("2025-11-05", "2025-11-05", "bhai_dooj", 1.1),
        ("2025-12-25", "2025-12-25", "christmas", 1.1),
    ]
    
    @classmethod
    def get_device_score(cls, user_agent: str) -> float:
        """Extract device brand and return WTP score"""
        ua_lower = user_agent.lower()
        
        # Check each device pattern
        for device, score in sorted(cls.DEVICE_SCORES.items(), key=lambda x: -len(x[0])):
            if device in ua_lower:
                return score
        
        # Desktop/laptop detection
        if "windows" in ua_lower:
            return 0.6
        elif "macintosh" in ua_lower or "mac os" in ua_lower:
            return 0.9
        elif "linux" in ua_lower:
            return 0.65
        
        return 0.5  # Unknown
    
    @classmethod
    def get_city_tier(cls, city: str) -> tuple:
        """Return (tier, multiplier) for city"""
        city_lower = city.lower().strip()
        
        if city_lower in cls.METRO_CITIES:
            return ("metro", 1.0)
        elif city_lower in cls.TIER2_CITIES:
            return ("tier2", 0.75)
        else:
            return ("tier3", 0.5)
    
    @classmethod
    def get_state_multiplier(cls, state: str) -> float:
        """Get spending multiplier for state"""
        state_lower = state.lower().strip()
        return cls.STATE_MULTIPLIERS.get(state_lower, 0.5)
    
    @classmethod
    def get_time_multiplier(cls) -> tuple:
        """Get multiplier based on time factors"""
        now = datetime.now()
        multiplier = 1.0
        reasons = []
        
        # Day of month (salary cycle)
        day = now.day
        if 1 <= day <= 7:
            multiplier *= 1.15
            reasons.append("salary_week")
        elif 25 <= day <= 31:
            multiplier *= 0.85
            reasons.append("month_end")
        
        # Time of day
        hour = now.hour
        if 22 <= hour or hour <= 5:
            multiplier *= 1.1  # Late night = professionals
            reasons.append("late_night_professional")
        elif 10 <= hour <= 18:
            multiplier *= 1.05  # Business hours
            reasons.append("business_hours")
        
        # Day of week
        if now.weekday() >= 5:  # Weekend
            multiplier *= 0.95
            reasons.append("weekend")
        
        # Festival check
        today = now.strftime("%Y-%m-%d")
        for start, end, festival, fest_mult in cls.FESTIVALS_2025:
            if start <= today <= end:
                multiplier *= fest_mult
                reasons.append(f"festival_{festival}")
                break
        
        return (multiplier, reasons)
    
    @classmethod
    def calculate_price(cls, session_data: Dict) -> Dict:
        """
        Main pricing calculation using all signals.
        Returns price in INR with full reasoning.
        """
        signals = {}
        multipliers = []
        
        # 1. Device Signal
        user_agent = session_data.get("user_agent", "")
        device_score = cls.get_device_score(user_agent)
        signals["device_score"] = device_score
        signals["device_detected"] = cls._detect_device_name(user_agent)
        multipliers.append(("device", 0.5 + device_score * 0.8))  # 0.5x to 1.3x
        
        # 2. Geographic Signal
        city = session_data.get("city", "")
        state = session_data.get("state", "")
        
        city_tier, city_mult = cls.get_city_tier(city)
        state_mult = cls.get_state_multiplier(state)
        geo_mult = (city_mult + state_mult) / 2
        
        signals["city"] = city
        signals["city_tier"] = city_tier
        signals["state"] = state
        signals["geo_multiplier"] = geo_mult
        multipliers.append(("geography", 0.4 + geo_mult * 0.9))  # 0.4x to 1.3x
        
        # 3. Time Signal
        time_mult, time_reasons = cls.get_time_multiplier()
        signals["time_multiplier"] = time_mult
        signals["time_factors"] = time_reasons
        multipliers.append(("time", time_mult))
        
        # 4. Document Signal
        doc_type = session_data.get("document_type", "unknown")
        page_count = session_data.get("page_count", 1)
        edit_count = session_data.get("edit_count", 0)
        
        # Business docs = higher WTP
        business_docs = ["invoice", "contract", "agreement", "proposal", "report", "resume", "cv"]
        is_business = any(b in doc_type.lower() for b in business_docs)
        doc_mult = 1.2 if is_business else 0.9
        
        # More pages/edits = more value = higher price
        complexity_mult = 1.0 + (min(page_count, 20) / 50) + (min(edit_count, 10) / 20)
        
        signals["document_type"] = doc_type
        signals["is_business_doc"] = is_business
        signals["page_count"] = page_count
        signals["edit_count"] = edit_count
        signals["complexity_multiplier"] = complexity_mult
        multipliers.append(("document", doc_mult * complexity_mult))
        
        # 5. Behavior Signal
        time_on_site = session_data.get("time_on_site_seconds", 0)
        pages_viewed = session_data.get("pages_viewed", 1)
        
        # More time invested = more committed = can charge more
        engagement_mult = 1.0
        if time_on_site > 300:  # 5+ minutes
            engagement_mult = 1.15
        elif time_on_site > 120:  # 2+ minutes
            engagement_mult = 1.08
        elif time_on_site < 30:  # Quick bounce risk
            engagement_mult = 0.85
        
        signals["time_on_site"] = time_on_site
        signals["engagement_multiplier"] = engagement_mult
        multipliers.append(("engagement", engagement_mult))
        
        # 6. Screen/Hardware Signal
        screen_width = session_data.get("screen_width", 0)
        device_memory = session_data.get("device_memory", 4)
        
        hardware_mult = 1.0
        if screen_width >= 1440:  # High-res display
            hardware_mult = 1.1
        elif screen_width <= 720:  # Budget phone
            hardware_mult = 0.85
        
        if device_memory >= 8:
            hardware_mult *= 1.1
        elif device_memory <= 2:
            hardware_mult *= 0.8
        
        signals["screen_width"] = screen_width
        signals["device_memory"] = device_memory
        signals["hardware_multiplier"] = hardware_mult
        multipliers.append(("hardware", hardware_mult))
        
        # 7. Return User Signal
        is_returning = session_data.get("is_returning", False)
        past_conversions = session_data.get("past_conversions", 0)
        
        if past_conversions > 0:
            loyalty_mult = 1.1  # Known converter
        elif is_returning:
            loyalty_mult = 1.05  # Came back
        else:
            loyalty_mult = 0.95  # New user - be competitive
        
        signals["is_returning"] = is_returning
        signals["past_conversions"] = past_conversions
        multipliers.append(("loyalty", loyalty_mult))
        
        # ==================== FINAL CALCULATION ====================
        
        # Calculate combined multiplier
        final_mult = 1.0
        for name, mult in multipliers:
            final_mult *= mult
        
        # Apply to base price
        base_price = cls.DEFAULT_PRICE_INR  # ₹79
        calculated_price = base_price * final_mult
        
        # Apply bounds
        final_price = max(cls.MIN_PRICE_INR, min(cls.MAX_PRICE_INR, calculated_price))
        
        # Round to psychological price points
        final_price = cls._round_to_price_point(final_price)
        
        # Calculate USD equivalent (approximate)
        usd_price = round(final_price / 83, 2)
        
        return {
            "price_inr": final_price,
            "price_usd": usd_price,
            "base_price": base_price,
            "final_multiplier": round(final_mult, 3),
            "signals": signals,
            "multipliers": {name: round(mult, 3) for name, mult in multipliers},
            "confidence": cls._calculate_confidence(signals),
        }
    
    @classmethod
    def _detect_device_name(cls, user_agent: str) -> str:
        """Extract readable device name from user agent"""
        ua_lower = user_agent.lower()
        
        if "iphone" in ua_lower:
            return "iPhone"
        elif "ipad" in ua_lower:
            return "iPad"
        elif "macintosh" in ua_lower:
            return "Mac"
        elif "oneplus" in ua_lower:
            return "OnePlus"
        elif "samsung" in ua_lower:
            if "galaxy s" in ua_lower:
                return "Samsung Galaxy S"
            elif "galaxy a" in ua_lower:
                return "Samsung Galaxy A"
            elif "galaxy m" in ua_lower:
                return "Samsung Galaxy M"
            return "Samsung"
        elif "redmi" in ua_lower:
            return "Redmi"
        elif "xiaomi" in ua_lower:
            return "Xiaomi"
        elif "poco" in ua_lower:
            return "Poco"
        elif "realme" in ua_lower:
            return "Realme"
        elif "oppo" in ua_lower:
            return "Oppo"
        elif "vivo" in ua_lower:
            return "Vivo"
        elif "pixel" in ua_lower:
            return "Google Pixel"
        elif "windows" in ua_lower:
            return "Windows PC"
        elif "linux" in ua_lower:
            return "Linux PC"
        
        return "Unknown Device"
    
    @classmethod
    def _round_to_price_point(cls, price: float) -> int:
        """Round to Indian psychological price points"""
        price_points = [29, 39, 49, 59, 69, 79, 89, 99, 119, 149, 179, 199, 249, 299]
        
        # Find closest price point
        closest = min(price_points, key=lambda x: abs(x - price))
        return closest
    
    @classmethod
    def _calculate_confidence(cls, signals: Dict) -> float:
        """Calculate confidence in pricing decision (0-1)"""
        confidence = 0.5  # Base confidence
        
        # More signals = more confidence
        if signals.get("city"):
            confidence += 0.1
        if signals.get("device_score", 0.5) != 0.5:
            confidence += 0.15
        if signals.get("time_on_site", 0) > 60:
            confidence += 0.1
        if signals.get("is_returning"):
            confidence += 0.1
        
        return min(0.95, confidence)
    
    @classmethod
    async def get_ai_price_adjustment(cls, session_data: Dict, base_result: Dict) -> Dict:
        """
        Use Claude to analyze edge cases and adjust pricing.
        This adds human-like reasoning to the algorithmic pricing.
        """
        if not ANTHROPIC_API_KEY:
            return base_result
        
        # Only use AI for uncertain cases
        if base_result["confidence"] > 0.8:
            return base_result
        
        try:
            # Get recent similar outcomes for context
            similar_outcomes = cls._get_similar_outcomes(session_data)
            
            prompt = f"""You are a pricing AI for an Indian PDF editing service. Analyze this user and suggest if the calculated price should be adjusted.

USER DATA:
- Device: {base_result['signals'].get('device_detected', 'Unknown')}
- City: {base_result['signals'].get('city', 'Unknown')}, {base_result['signals'].get('state', 'Unknown')}
- City Tier: {base_result['signals'].get('city_tier', 'Unknown')}
- Document Type: {base_result['signals'].get('document_type', 'Unknown')}
- Pages: {base_result['signals'].get('page_count', 1)}
- Edits Made: {base_result['signals'].get('edit_count', 0)}
- Time on Site: {base_result['signals'].get('time_on_site', 0)} seconds
- Screen Width: {base_result['signals'].get('screen_width', 0)}px
- Device Memory: {base_result['signals'].get('device_memory', 0)}GB

CALCULATED PRICE: ₹{base_result['price_inr']}

RECENT SIMILAR USERS (last 20 with similar profile):
{json.dumps(similar_outcomes[:5], indent=2) if similar_outcomes else 'No historical data yet'}

Based on this data, should I:
1. KEEP the price at ₹{base_result['price_inr']}
2. LOWER to a specific amount (suggest amount)
3. RAISE to a specific amount (suggest amount)

Respond with JSON only:
{{"action": "keep|lower|raise", "new_price": <number or null>, "reason": "brief explanation"}}"""

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 200,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                
                if response.status_code == 200:
                    ai_text = response.json().get("content", [{}])[0].get("text", "{}")
                    
                    # Parse AI response
                    try:
                        if "```" in ai_text:
                            ai_text = ai_text.split("```")[1].replace("json", "").strip()
                        ai_decision = json.loads(ai_text)
                        
                        if ai_decision.get("action") == "lower" and ai_decision.get("new_price"):
                            new_price = cls._round_to_price_point(ai_decision["new_price"])
                            base_result["price_inr"] = new_price
                            base_result["price_usd"] = round(new_price / 83, 2)
                            base_result["ai_adjusted"] = True
                            base_result["ai_reason"] = ai_decision.get("reason", "")
                        elif ai_decision.get("action") == "raise" and ai_decision.get("new_price"):
                            new_price = cls._round_to_price_point(ai_decision["new_price"])
                            base_result["price_inr"] = new_price
                            base_result["price_usd"] = round(new_price / 83, 2)
                            base_result["ai_adjusted"] = True
                            base_result["ai_reason"] = ai_decision.get("reason", "")
                    except:
                        pass
                        
        except Exception as e:
            print(f"AI pricing adjustment error: {e}")
        
        return base_result
    
    @classmethod
    def _get_similar_outcomes(cls, session_data: Dict) -> List[Dict]:
        """Find similar past sessions to learn from"""
        similar = []
        
        device_score = cls.get_device_score(session_data.get("user_agent", ""))
        city_tier = cls.get_city_tier(session_data.get("city", ""))[0]
        
        for outcome in pricing_outcomes[-1000:]:  # Check last 1000
            o_device = outcome.get("signals", {}).get("device_score", 0)
            o_tier = outcome.get("signals", {}).get("city_tier", "")
            
            # Similar if same tier and similar device score
            if o_tier == city_tier and abs(o_device - device_score) < 0.2:
                similar.append({
                    "price_shown": outcome.get("price_inr"),
                    "converted": outcome.get("converted", False),
                    "device": outcome.get("signals", {}).get("device_detected"),
                    "city": outcome.get("signals", {}).get("city"),
                })
        
        return similar[-20:]  # Last 20 similar
    
    @classmethod
    def record_outcome(cls, session_id: str, converted: bool, time_on_pricing_page: int = 0):
        """Record whether user converted or bounced - this is how we learn"""
        if session_id not in sessions:
            return
        
        session = sessions[session_id]
        pricing_data = session.get("pricing_result", {})
        
        outcome = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "converted": converted,
            "price_inr": pricing_data.get("price_inr"),
            "signals": pricing_data.get("signals", {}),
            "multipliers": pricing_data.get("multipliers", {}),
            "time_on_pricing_page": time_on_pricing_page,
            "user_agent": session.get("user_agent", ""),
        }
        
        pricing_outcomes.append(outcome)
        save_pricing_data()
        
        # Log for analysis
        status = "✅ CONVERTED" if converted else "❌ BOUNCED"
        print(f"{status} | ₹{pricing_data.get('price_inr')} | {pricing_data.get('signals', {}).get('device_detected')} | {pricing_data.get('signals', {}).get('city')}")


# ============================================================================
# PDF PROCESSING
# ============================================================================

def pdf_to_images(pdf_bytes: bytes, dpi: int = 150) -> List[str]:
    """Convert PDF pages to base64 images"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    
    for page in doc:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img_b64 = base64.b64encode(img_bytes).decode()
        images.append(img_b64)
    
    doc.close()
    return images


def extract_text_with_ocr(pdf_bytes: bytes, dpi: int = 150) -> List[Dict]:
    """
    Extract text using OCR (Optical Character Recognition).
    Works on ALL PDFs including scanned documents and images.

    Returns text blocks with precise bounding boxes from OCR.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []

    for page_num, page in enumerate(doc):
        # Render page as image at specified DPI
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")

        # Convert to PIL Image for OCR
        img = Image.open(io.BytesIO(img_data))

        # Run OCR with bounding box data
        # Output includes: level, page_num, block_num, par_num, line_num, word_num,
        #                  left, top, width, height, conf, text
        ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        blocks = []
        full_text = []

        n_boxes = len(ocr_data['text'])
        for i in range(n_boxes):
            text = ocr_data['text'][i].strip()
            conf = int(ocr_data['conf'][i])

            # Only include text with reasonable confidence (> 30%)
            if text and conf > 30:
                # OCR returns coordinates in pixels at render DPI
                # We keep them as-is since our display also uses same DPI
                x = ocr_data['left'][i]
                y = ocr_data['top'][i]
                w = ocr_data['width'][i]
                h = ocr_data['height'][i]

                # Estimate font size from height (rough approximation)
                font_size = h * 0.75  # Typical text height to font size ratio

                blocks.append({
                    "text": text,
                    "x": x,
                    "y": y,
                    "width": w,
                    "height": h,
                    "fontSize": font_size,
                    "color": 0,  # OCR doesn't detect color, default to black
                    "font": "helv",  # Default font
                    "flags": 0,
                    "confidence": conf,
                    # PDF coordinates for editing (convert back from display DPI)
                    "pdf_x": x * 72 / dpi,
                    "pdf_y": y * 72 / dpi,
                    "pdf_fontSize": font_size * 72 / dpi,
                })
                full_text.append(text)

        # Get page dimensions in display pixels
        page_width = pix.width
        page_height = pix.height

        pages_data.append({
            "page": page_num + 1,
            "width": page_width,
            "height": page_height,
            "pdf_width": page.rect.width,
            "pdf_height": page.rect.height,
            "blocks": blocks,
            "text": " ".join(full_text),
        })

    doc.close()
    return pages_data


def extract_text_with_positions(pdf_bytes: bytes) -> List[Dict]:
    """
    Extract text using OCR as the primary method.
    Falls back to PyMuPDF extraction if OCR fails.
    """
    try:
        # Use OCR as the core method
        return extract_text_with_ocr(pdf_bytes)
    except Exception as e:
        print(f"OCR failed, falling back to PyMuPDF: {e}")
        # Fallback to original method
        return extract_text_pymupdf(pdf_bytes)


def extract_text_pymupdf(pdf_bytes: bytes) -> List[Dict]:
    """Original PyMuPDF-based text extraction (fallback)"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []
    scale = 150 / 72  # Scale to match display DPI

    for page_num, page in enumerate(doc):
        page_dict = page.get_text("dict")
        blocks = []

        for block in page_dict.get("blocks", []):
            if block.get("type") == 0:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            bbox = span.get("bbox", [0, 0, 0, 0])
                            blocks.append({
                                "text": text,
                                "x": bbox[0] * scale,
                                "y": bbox[1] * scale,
                                "width": (bbox[2] - bbox[0]) * scale,
                                "height": (bbox[3] - bbox[1]) * scale,
                                "fontSize": span.get("size", 12) * scale,
                                "color": span.get("color", 0),
                                "font": span.get("font", "helv"),
                                "flags": span.get("flags", 0),
                                "pdf_x": bbox[0],
                                "pdf_y": bbox[1],
                                "pdf_fontSize": span.get("size", 12),
                            })

        pages_data.append({
            "page": page_num + 1,
            "width": page_dict.get("width", 612) * scale,
            "height": page_dict.get("height", 792) * scale,
            "pdf_width": page_dict.get("width", 612),
            "pdf_height": page_dict.get("height", 792),
            "blocks": blocks,
            "text": page.get_text("text"),
        })

    doc.close()
    return pages_data


def map_font_name(original_font: str, flags: int = 0) -> str:
    """
    Map original PDF fonts to PyMuPDF Base-14 fonts.
    Base-14 fonts are always available without embedding.

    Flags: 1=superscript, 2=italic, 4=serif, 8=monospace, 16=bold
    """
    font_lower = original_font.lower()
    is_bold = bool(flags & 16) or "bold" in font_lower or "black" in font_lower
    is_italic = bool(flags & 2) or "italic" in font_lower or "oblique" in font_lower
    is_mono = bool(flags & 8) or "courier" in font_lower or "mono" in font_lower or "consol" in font_lower
    is_serif = bool(flags & 4) or "times" in font_lower or "roman" in font_lower or "georgia" in font_lower or "serif" in font_lower

    # Monospace fonts -> Courier family
    if is_mono:
        if is_bold and is_italic:
            return "cobi"
        elif is_bold:
            return "cobo"
        elif is_italic:
            return "coit"
        return "cour"

    # Serif fonts -> Times family
    if is_serif:
        if is_bold and is_italic:
            return "tibi"
        elif is_bold:
            return "tibo"
        elif is_italic:
            return "tiit"
        return "tiro"

    # Default: Sans-serif -> Helvetica family
    if is_bold and is_italic:
        return "hebi"
    elif is_bold:
        return "hebo"
    elif is_italic:
        return "heit"
    return "helv"


async def analyze_document_with_ai(images: List[str], text_data: List[Dict]) -> Dict:
    """Use Claude Vision to analyze document"""
    if not ANTHROPIC_API_KEY or not images:
        return {"type": "unknown", "ai_available": False}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": images[0],
                                }
                            },
                            {
                                "type": "text",
                                "text": """Analyze this document and return JSON:
{
    "documentType": "invoice/contract/form/letter/report/resume/certificate/other",
    "title": "document title if visible",
    "language": "detected language",
    "isBusinessDocument": true/false,
    "editableFields": [
        {"fieldName": "name", "fieldType": "date/name/address/phone/email/amount", "currentValue": "value"}
    ],
    "suggestedEdits": ["common edit 1", "common edit 2"]
}
Return ONLY valid JSON."""
                            }
                        ]
                    }]
                }
            )
            
            if response.status_code == 200:
                ai_text = response.json().get("content", [{}])[0].get("text", "{}")
                try:
                    if "```" in ai_text:
                        ai_text = ai_text.split("```")[1].replace("json", "").strip()
                    analysis = json.loads(ai_text)
                    analysis["ai_available"] = True
                    return analysis
                except:
                    pass
        except Exception as e:
            print(f"AI analysis error: {e}")
    
    return {"type": "unknown", "ai_available": False}


async def apply_ai_edit(doc_id: str, instruction: str) -> Dict:
    """Apply natural language edit"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "AI not configured")
    
    doc_data = documents[doc_id]
    pdf_bytes = doc_data["pdf_bytes"]
    text_data = extract_text_with_positions(pdf_bytes)
    full_text = "\n\n".join([f"=== PAGE {p['page']} ===\n{p['text']}" for p in text_data])
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{
                    "role": "user",
                    "content": f"""You are a PDF editor. Given the document and instruction, return exact text replacements.

DOCUMENT:
{full_text[:8000]}

INSTRUCTION: {instruction}

Return JSON:
{{"replacements": [{{"find": "exact text", "replace": "new text"}}], "explanation": "what was changed"}}

Rules:
- Match text EXACTLY as it appears
- If unclear, return empty replacements with explanation
Return ONLY valid JSON."""
                }]
            }
        )
        
        if response.status_code != 200:
            raise HTTPException(500, "AI request failed")
        
        ai_text = response.json().get("content", [{}])[0].get("text", "{}")
        try:
            if "```" in ai_text:
                ai_text = ai_text.split("```")[1].replace("json", "").strip()
            edit_plan = json.loads(ai_text)
        except:
            return {"success": False, "error": "Could not parse AI response"}
    
    replacements = edit_plan.get("replacements", [])
    if not replacements:
        return {"success": False, "message": edit_plan.get("explanation", "No changes identified")}
    
    # Apply edits
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    changes = 0
    
    for repl in replacements:
        find_text = repl.get("find", "")
        replace_text = repl.get("replace", "")
        if not find_text:
            continue
        
        for page in doc:
            for rect in page.search_for(find_text):
                text_dict = page.get_text("dict", clip=rect)
                font_size = 11
                color = (0, 0, 0)
                original_font = "helv"
                font_flags = 0

                for block in text_dict.get("blocks", []):
                    if block.get("type") == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                font_size = span.get("size", 11)
                                original_font = span.get("font", "helv")
                                font_flags = span.get("flags", 0)
                                c = span.get("color", 0)
                                if isinstance(c, int):
                                    color = (((c >> 16) & 0xFF) / 255, ((c >> 8) & 0xFF) / 255, (c & 0xFF) / 255)
                                break

                # Map original font to Base-14
                mapped_font = map_font_name(original_font, font_flags)

                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()
                page.insert_text(fitz.Point(rect.x0, rect.y0 + font_size * 0.82), replace_text, fontsize=font_size, color=color, fontname=mapped_font)
                changes += 1
    
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True, clean=True)
    doc.close()
    
    new_bytes = output.getvalue()
    documents[doc_id]["pdf_bytes"] = new_bytes
    documents[doc_id]["pages"] = pdf_to_images(new_bytes)
    documents[doc_id]["edit_count"] = documents[doc_id].get("edit_count", 0) + changes
    
    return {"success": True, "changes": changes, "explanation": edit_plan.get("explanation", "")}


# ============================================================================
# API ROUTES
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    with open(TEMPLATES_DIR / "index.html") as f:
        return f.read()


@app.get("/edit", response_class=HTMLResponse)
async def editor():
    with open(TEMPLATES_DIR / "editor.html") as f:
        return f.read()


@app.get("/api/config")
async def get_config():
    return {"ai_enabled": bool(ANTHROPIC_API_KEY), "version": "2.0.0", "region": "IN"}


# ==================== SESSION & PRICING ====================

@app.post("/api/session/start")
async def start_session(request: Request, data: str = Form(...)):
    """Start session and collect initial data"""
    try:
        client_data = json.loads(data)
    except:
        client_data = {}
    
    session_id = str(uuid.uuid4())
    
    # Collect server-side data
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "")
    
    sessions[session_id] = {
        "created_at": datetime.now().isoformat(),
        "ip": client_ip,
        "user_agent": user_agent,
        "referrer": request.headers.get("referer", ""),
        "accept_language": request.headers.get("accept-language", ""),
        **client_data,
    }
    
    return {"session_id": session_id}


@app.post("/api/session/{session_id}/update")
async def update_session(session_id: str, data: str = Form(...)):
    """Update session with additional data"""
    if session_id not in sessions:
        sessions[session_id] = {}
    
    try:
        update_data = json.loads(data)
        sessions[session_id].update(update_data)
    except:
        pass
    
    return {"success": True}


@app.post("/api/session/{session_id}/geo")
async def update_geo(session_id: str, city: str = Form(""), state: str = Form(""), country: str = Form("")):
    """Update geographic data (from client-side geo API)"""
    if session_id in sessions:
        sessions[session_id]["city"] = city
        sessions[session_id]["state"] = state
        sessions[session_id]["country"] = country
    return {"success": True}


@app.post("/api/pricing/calculate")
async def calculate_price(session_id: str = Form(...), doc_id: str = Form(None)):
    """Calculate optimal price for this user"""
    session_data = sessions.get(session_id, {})
    
    # Add document data if available
    if doc_id and doc_id in documents:
        doc = documents[doc_id]
        session_data["document_type"] = doc.get("analysis", {}).get("documentType", "unknown")
        session_data["page_count"] = doc.get("page_count", 1)
        session_data["edit_count"] = doc.get("edit_count", 0)
        session_data["is_business_doc"] = doc.get("analysis", {}).get("isBusinessDocument", False)
    
    # Calculate price
    result = IndiaPricingBrain.calculate_price(session_data)
    
    # Optional: AI adjustment for edge cases
    result = await IndiaPricingBrain.get_ai_price_adjustment(session_data, result)
    
    # Store result in session
    if session_id in sessions:
        sessions[session_id]["pricing_result"] = result
    
    return result


@app.post("/api/pricing/shown")
async def pricing_shown(session_id: str = Form(...)):
    """Track when pricing was shown to user"""
    if session_id in sessions:
        sessions[session_id]["pricing_shown_at"] = datetime.now().isoformat()
    return {"success": True}


@app.post("/api/pricing/outcome")
async def pricing_outcome(
    session_id: str = Form(...),
    converted: bool = Form(...),
    time_on_pricing: int = Form(0)
):
    """Record conversion or bounce - THIS IS HOW WE LEARN"""
    IndiaPricingBrain.record_outcome(session_id, converted, time_on_pricing)
    return {"success": True, "recorded": True}


@app.get("/api/pricing/stats")
async def pricing_stats():
    """Get pricing analytics"""
    if not pricing_outcomes:
        return {"total": 0, "conversions": 0, "rate": 0}
    
    total = len(pricing_outcomes)
    conversions = sum(1 for o in pricing_outcomes if o.get("converted"))
    
    # Recent stats (last 100)
    recent = pricing_outcomes[-100:]
    recent_conversions = sum(1 for o in recent if o.get("converted"))
    
    # By device tier
    device_stats = {}
    for o in pricing_outcomes[-500:]:
        device = o.get("signals", {}).get("device_detected", "Unknown")
        if device not in device_stats:
            device_stats[device] = {"total": 0, "converted": 0}
        device_stats[device]["total"] += 1
        if o.get("converted"):
            device_stats[device]["converted"] += 1
    
    # By city tier
    tier_stats = {}
    for o in pricing_outcomes[-500:]:
        tier = o.get("signals", {}).get("city_tier", "unknown")
        if tier not in tier_stats:
            tier_stats[tier] = {"total": 0, "converted": 0, "avg_price": []}
        tier_stats[tier]["total"] += 1
        tier_stats[tier]["avg_price"].append(o.get("price_inr", 79))
        if o.get("converted"):
            tier_stats[tier]["converted"] += 1
    
    # Calculate averages
    for tier in tier_stats:
        prices = tier_stats[tier]["avg_price"]
        tier_stats[tier]["avg_price"] = sum(prices) / len(prices) if prices else 0
    
    return {
        "total": total,
        "conversions": conversions,
        "rate": round(conversions / total * 100, 2) if total else 0,
        "recent_rate": round(recent_conversions / len(recent) * 100, 2) if recent else 0,
        "by_device": device_stats,
        "by_tier": tier_stats,
    }


# ==================== DOCUMENT ROUTES ====================

@app.post("/api/upload")
async def upload_pdf(request: Request, file: UploadFile = File(...), session_id: str = Form(None)):
    """Upload and analyze PDF, convert to Word for editing"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Only PDF files allowed")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")

    doc_id = str(uuid.uuid4())
    images = pdf_to_images(content)
    text_data = extract_text_with_positions(content)

    documents[doc_id] = {
        "pdf_bytes": content,
        "filename": file.filename,
        "pages": images,
        "text_data": text_data,
        "page_count": len(images),
        "edit_count": 0,
        "uploaded_at": datetime.now().isoformat(),
        "docx_bytes": None,  # Will be populated after Adobe conversion
        "html_content": None,  # Editable HTML content
        "conversion_status": "pending",  # pending, converting, ready, failed
    }

    # Link to session
    if session_id and session_id in sessions:
        sessions[session_id]["doc_id"] = doc_id
        sessions[session_id]["page_count"] = len(images)

    return {"success": True, "docId": doc_id, "pageCount": len(images), "filename": file.filename}


@app.post("/api/convert/{doc_id}")
async def convert_to_word(doc_id: str):
    """Convert PDF to Word using LibreOffice (called after upload)"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc = documents[doc_id]

    if doc.get("conversion_status") == "ready":
        return {"success": True, "status": "ready", "message": "Already converted"}

    if doc.get("conversion_status") == "converting":
        return {"success": True, "status": "converting", "message": "Conversion in progress"}

    try:
        documents[doc_id]["conversion_status"] = "converting"

        # Convert PDF to Word via LibreOffice (runs in thread pool to not block)
        loop = asyncio.get_event_loop()
        docx_bytes = await loop.run_in_executor(
            None, LibreOfficeConverter.pdf_to_docx, doc["pdf_bytes"]
        )

        # Convert Word to HTML for editing
        html_content = docx_to_html(docx_bytes)

        documents[doc_id]["docx_bytes"] = docx_bytes
        documents[doc_id]["html_content"] = html_content
        documents[doc_id]["conversion_status"] = "ready"

        return {"success": True, "status": "ready", "message": "Conversion complete"}

    except Exception as e:
        print(f"Conversion error: {e}")
        documents[doc_id]["conversion_status"] = "failed"
        return {"success": False, "status": "failed", "message": str(e)}


@app.get("/api/document/{doc_id}/status")
async def get_conversion_status(doc_id: str):
    """Check conversion status"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc = documents[doc_id]
    return {
        "status": doc.get("conversion_status", "pending"),
        "hasHtml": doc.get("html_content") is not None,
    }


@app.get("/api/document/{doc_id}/html")
async def get_html_content(doc_id: str):
    """Get editable HTML content"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc = documents[doc_id]

    if doc.get("conversion_status") != "ready":
        raise HTTPException(400, "Document not yet converted")

    return {
        "html": doc.get("html_content", ""),
        "pageCount": doc.get("page_count", 1),
    }


@app.post("/api/document/{doc_id}/html")
async def save_html_content(doc_id: str, html: str = Form(...), session_id: str = Form(None)):
    """Save edited HTML content and regenerate PDF using LibreOffice"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc = documents[doc_id]

    try:
        # Update HTML content
        documents[doc_id]["html_content"] = html

        # Convert HTML to DOCX via LibreOffice (in thread pool)
        loop = asyncio.get_event_loop()
        new_docx = await loop.run_in_executor(
            None, LibreOfficeConverter.html_to_docx, html
        )
        documents[doc_id]["docx_bytes"] = new_docx

        # Convert DOCX to PDF via LibreOffice
        new_pdf = await loop.run_in_executor(
            None, LibreOfficeConverter.docx_to_pdf, new_docx
        )
        documents[doc_id]["pdf_bytes"] = new_pdf

        # Regenerate images
        documents[doc_id]["pages"] = pdf_to_images(new_pdf)
        documents[doc_id]["text_data"] = extract_text_with_positions(new_pdf)
        documents[doc_id]["edit_count"] = doc.get("edit_count", 0) + 1

        # Update session
        if session_id and session_id in sessions:
            sessions[session_id]["edit_count"] = sessions[session_id].get("edit_count", 0) + 1

        return {
            "success": True,
            "message": "Document updated",
            "pageCount": len(documents[doc_id]["pages"]),
        }

    except Exception as e:
        print(f"Save error: {e}")
        raise HTTPException(500, f"Failed to save: {str(e)}")


@app.post("/api/analyze/{doc_id}")
async def analyze_document(doc_id: str):
    """AI analysis of document"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    
    doc = documents[doc_id]
    analysis = await analyze_document_with_ai(doc["pages"], doc["text_data"])
    documents[doc_id]["analysis"] = analysis
    
    return {"success": True, "analysis": analysis}


@app.get("/api/document/{doc_id}")
async def get_document(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    doc = documents[doc_id]
    return {
        "docId": doc_id,
        "filename": doc.get("filename"),
        "pageCount": doc.get("page_count"),
        "analysis": doc.get("analysis"),
        "editCount": doc.get("edit_count", 0),
    }


@app.get("/api/document/{doc_id}/page/{page_num}")
async def get_page(doc_id: str, page_num: int):
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    doc = documents[doc_id]
    pages = doc.get("pages", [])
    if page_num < 1 or page_num > len(pages):
        raise HTTPException(400, "Invalid page")
    return {"page": page_num, "totalPages": len(pages), "image": pages[page_num - 1]}


@app.get("/api/document/{doc_id}/page/{page_num}/text")
async def get_page_text(doc_id: str, page_num: int):
    """Get text blocks with positions for overlay rendering (OCR-based)"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc = documents[doc_id]
    text_data = doc.get("text_data", [])

    if page_num < 1 or page_num > len(text_data):
        raise HTTPException(400, "Invalid page")

    page_data = text_data[page_num - 1]

    # OCR returns coordinates already in display pixels (150 DPI)
    # No additional scaling needed - just pass through the blocks
    blocks = []
    for block in page_data.get("blocks", []):
        blocks.append({
            "text": block["text"],
            "x": block["x"],
            "y": block["y"],
            "width": block["width"],
            "height": block["height"],
            "fontSize": block["fontSize"],
            "color": block.get("color", 0),
            "font": block.get("font", "helv"),
            "flags": block.get("flags", 0),
            "confidence": block.get("confidence", 100),
            # PDF coordinates for editing
            "pdf_x": block.get("pdf_x", block["x"] * 72 / 150),
            "pdf_y": block.get("pdf_y", block["y"] * 72 / 150),
            "pdf_fontSize": block.get("pdf_fontSize", block["fontSize"] * 72 / 150),
        })

    return {
        "page": page_num,
        "width": page_data.get("width", 918),  # 612 * 150/72
        "height": page_data.get("height", 1188),  # 792 * 150/72
        "blocks": blocks,
    }


@app.post("/api/edit/{doc_id}/direct")
async def direct_edit(
    doc_id: str,
    page: int = Form(...),
    original_text: str = Form(...),
    new_text: str = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    font_size: float = Form(...),
    font_name: str = Form("helv"),
    color: int = Form(0),
    flags: int = Form(0),
    session_id: str = Form(None),
):
    """Direct text replacement at specific location with font preservation"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc_data = documents[doc_id]
    pdf_bytes = doc_data["pdf_bytes"]

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    if page < 1 or page > len(doc):
        doc.close()
        raise HTTPException(400, "Invalid page")

    pdf_page = doc[page - 1]
    changes = 0

    # Search for the text and replace at matching location
    for rect in pdf_page.search_for(original_text):
        # Check if this rect matches our target location (within tolerance)
        if abs(rect.x0 - x) < 5 and abs(rect.y0 - y) < 5:
            # Convert color int to RGB tuple
            if isinstance(color, int):
                r = ((color >> 16) & 0xFF) / 255
                g = ((color >> 8) & 0xFF) / 255
                b = (color & 0xFF) / 255
                color_tuple = (r, g, b)
            else:
                color_tuple = (0, 0, 0)

            # Map font to Base-14
            mapped_font = map_font_name(font_name, flags)

            # Redact original text
            pdf_page.add_redact_annot(rect, fill=(1, 1, 1))
            pdf_page.apply_redactions()

            # Insert new text with preserved styling
            pdf_page.insert_text(
                fitz.Point(rect.x0, rect.y0 + font_size * 0.82),
                new_text,
                fontsize=font_size,
                color=color_tuple,
                fontname=mapped_font,
            )
            changes += 1
            break

    # If no exact location match, try simple search and replace
    if changes == 0:
        for rect in pdf_page.search_for(original_text):
            if isinstance(color, int):
                r = ((color >> 16) & 0xFF) / 255
                g = ((color >> 8) & 0xFF) / 255
                b = (color & 0xFF) / 255
                color_tuple = (r, g, b)
            else:
                color_tuple = (0, 0, 0)

            mapped_font = map_font_name(font_name, flags)

            pdf_page.add_redact_annot(rect, fill=(1, 1, 1))
            pdf_page.apply_redactions()

            pdf_page.insert_text(
                fitz.Point(rect.x0, rect.y0 + font_size * 0.82),
                new_text,
                fontsize=font_size,
                color=color_tuple,
                fontname=mapped_font,
            )
            changes += 1
            break

    if changes == 0:
        doc.close()
        return {"success": False, "message": "Text not found on page"}

    # Save updated PDF
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True, clean=True)
    doc.close()

    new_bytes = output.getvalue()
    documents[doc_id]["pdf_bytes"] = new_bytes
    documents[doc_id]["pages"] = pdf_to_images(new_bytes)
    documents[doc_id]["text_data"] = extract_text_with_positions(new_bytes)
    documents[doc_id]["edit_count"] = documents[doc_id].get("edit_count", 0) + changes

    # Update session
    if session_id and session_id in sessions:
        sessions[session_id]["edit_count"] = sessions[session_id].get("edit_count", 0) + changes

    # Return updated page image
    return {
        "success": True,
        "changes": changes,
        "image": documents[doc_id]["pages"][page - 1],
    }


@app.post("/api/edit/{doc_id}/add-text")
async def add_text(
    doc_id: str,
    page: int = Form(...),
    text: str = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    font_size: float = Form(12),
    font_name: str = Form("helv"),
    color: int = Form(0),
    session_id: str = Form(None),
):
    """Add new text at specific location"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")

    doc_data = documents[doc_id]
    pdf_bytes = doc_data["pdf_bytes"]

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    if page < 1 or page > len(doc):
        doc.close()
        raise HTTPException(400, "Invalid page")

    pdf_page = doc[page - 1]

    # Convert color int to RGB tuple
    if isinstance(color, int):
        r = ((color >> 16) & 0xFF) / 255
        g = ((color >> 8) & 0xFF) / 255
        b = (color & 0xFF) / 255
        color_tuple = (r, g, b)
    else:
        color_tuple = (0, 0, 0)

    # Insert text at position
    pdf_page.insert_text(
        fitz.Point(x, y),
        text,
        fontsize=font_size,
        color=color_tuple,
        fontname=font_name,
    )

    # Save updated PDF
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True, clean=True)
    doc.close()

    new_bytes = output.getvalue()
    documents[doc_id]["pdf_bytes"] = new_bytes
    documents[doc_id]["pages"] = pdf_to_images(new_bytes)
    documents[doc_id]["text_data"] = extract_text_with_positions(new_bytes)
    documents[doc_id]["edit_count"] = documents[doc_id].get("edit_count", 0) + 1

    if session_id and session_id in sessions:
        sessions[session_id]["edit_count"] = sessions[session_id].get("edit_count", 0) + 1

    return {
        "success": True,
        "message": "Text added",
        "image": documents[doc_id]["pages"][page - 1],
    }


@app.post("/api/edit/{doc_id}")
async def edit_document(doc_id: str, instruction: str = Form(...), session_id: str = Form(None)):
    """Apply AI edit"""
    result = await apply_ai_edit(doc_id, instruction)
    
    # Update session edit count
    if session_id and session_id in sessions:
        sessions[session_id]["edit_count"] = sessions[session_id].get("edit_count", 0) + result.get("changes", 0)
    
    return result


@app.post("/api/ask/{doc_id}")
async def ask_document(doc_id: str, question: str = Form(...)):
    """Ask question about document"""
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "AI not configured")
    
    doc = documents[doc_id]
    text_data = doc.get("text_data", [])
    full_text = "\n\n".join([p["text"] for p in text_data])
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": f"Document:\n{full_text[:10000]}\n\nQuestion: {question}\n\nAnswer based only on the document."}]
            }
        )
        
        if response.status_code == 200:
            answer = response.json().get("content", [{}])[0].get("text", "")
            return {"question": question, "answer": answer}
    
    raise HTTPException(500, "AI request failed")


@app.get("/api/download/{doc_id}")
async def download_pdf(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(404, "Document not found")
    
    doc = documents[doc_id]
    output_path = OUTPUT_DIR / f"{doc_id}.pdf"
    with open(output_path, "wb") as f:
        f.write(doc["pdf_bytes"])
    
    return FileResponse(output_path, media_type="application/pdf", filename=f"edited_{doc.get('filename', 'document.pdf')}")


# ==================== STATIC FILES ====================

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
