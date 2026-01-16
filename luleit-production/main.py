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
import httpx
import fitz
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


def extract_text_with_positions(pdf_bytes: bytes) -> List[Dict]:
    """Extract text blocks with positions"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []
    
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
                                "x": bbox[0],
                                "y": bbox[1],
                                "width": bbox[2] - bbox[0],
                                "height": bbox[3] - bbox[1],
                                "fontSize": span.get("size", 12),
                                "color": span.get("color", 0),
                            })
        
        pages_data.append({
            "page": page_num + 1,
            "width": page_dict.get("width", 612),
            "height": page_dict.get("height", 792),
            "blocks": blocks,
            "text": page.get_text("text"),
        })
    
    doc.close()
    return pages_data


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
                
                for block in text_dict.get("blocks", []):
                    if block.get("type") == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                font_size = span.get("size", 11)
                                c = span.get("color", 0)
                                if isinstance(c, int):
                                    color = (((c >> 16) & 0xFF) / 255, ((c >> 8) & 0xFF) / 255, (c & 0xFF) / 255)
                                break
                
                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()
                page.insert_text(fitz.Point(rect.x0, rect.y0 + font_size * 0.82), replace_text, fontsize=font_size, color=color, fontname="helv")
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
    """Upload and analyze PDF"""
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
    }
    
    # Link to session
    if session_id and session_id in sessions:
        sessions[session_id]["doc_id"] = doc_id
        sessions[session_id]["page_count"] = len(images)
    
    return {"success": True, "docId": doc_id, "pageCount": len(images), "filename": file.filename}


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
