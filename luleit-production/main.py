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
except:
    STRIPE_ENABLED = False

# ============ CONFIG ============
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "luleit2024")

# ============ PRICING CONFIG (PPP-adjusted with city intelligence + sunk cost) ============
# Base price in USD cents (500 = $5.00, 50 = $0.50)
BASE_PRICE_USD_CENTS = 299  # $2.99 base

# HIGH-VALUE CITIES - These get premium pricing regardless of country
# Format: "city_lowercase": multiplier_boost (added to country multiplier)
HIGH_VALUE_CITIES = {
    # ========== UAE - DETAILED ZONING ==========
    # Dubai - Premium Areas
    "dubai": 0.4,
    "downtown dubai": 0.7, "downtown": 0.6,
    "dubai marina": 0.7, "marina": 0.6,
    "palm jumeirah": 0.8, "palm": 0.7,
    "jumeirah beach residence": 0.7, "jbr": 0.7,
    "difc": 0.8, "dubai international financial centre": 0.8,
    "business bay": 0.65,
    "jumeirah": 0.6, "jumeirah 1": 0.65, "jumeirah 2": 0.6, "jumeirah 3": 0.6,
    "emirates hills": 0.8, "emirates living": 0.7,
    "arabian ranches": 0.65,
    "dubai hills": 0.7, "dubai hills estate": 0.7,
    "city walk": 0.65,
    "bluewaters": 0.7, "bluewaters island": 0.7,
    "al barsha": 0.5,
    "tecom": 0.55, "barsha heights": 0.55,
    "internet city": 0.6, "dubai internet city": 0.6,
    "media city": 0.6, "dubai media city": 0.6,
    "knowledge park": 0.55, "knowledge village": 0.55,
    "jlt": 0.55, "jumeirah lake towers": 0.55,
    "dubai silicon oasis": 0.5, "silicon oasis": 0.5,
    "deira": 0.35, "bur dubai": 0.35,
    "al quoz": 0.4,
    "motor city": 0.45, "sports city": 0.45,
    "mirdif": 0.45,
    "dubai south": 0.4, "expo city": 0.5,
    
    # Abu Dhabi - Premium Areas  
    "abu dhabi": 0.45,
    "al reem island": 0.7, "reem island": 0.7,
    "saadiyat island": 0.75, "saadiyat": 0.75,
    "yas island": 0.6, "yas": 0.55,
    "al maryah island": 0.7, "maryah island": 0.7,
    "corniche": 0.6, "al corniche": 0.6,
    "khalifa city": 0.5, "khalifa city a": 0.55,
    "al raha": 0.55, "al raha beach": 0.6,
    "al bateen": 0.6,
    "tourist club area": 0.5,
    "adgm": 0.75, "abu dhabi global market": 0.75,
    "masdar city": 0.6,
    "al ain": 0.35,
    
    # Sharjah (lower income than Dubai/Abu Dhabi)
    "sharjah": 0.25,
    "al majaz": 0.35,
    "al nahda": 0.3,
    "al khan": 0.35,
    
    # Other UAE
    "ajman": 0.2,
    "ras al khaimah": 0.3, "rak": 0.3,
    "fujairah": 0.25,
    
    # ========== SAUDI ARABIA - DETAILED ZONING ==========
    # Riyadh - Premium Areas
    "riyadh": 0.4,
    "olaya": 0.65, "al olaya": 0.65,
    "king abdullah financial district": 0.75, "kafd": 0.75,
    "diplomatic quarter": 0.7, "dq": 0.7,
    "hittin": 0.65, "al hittin": 0.65,
    "al nakheel": 0.6, "nakheel": 0.6,
    "al sahafa": 0.55,
    "al malqa": 0.6, "malqa": 0.6,
    "al yasmin": 0.55, "yasmin": 0.55,
    "al narjis": 0.5,
    "al rabwah": 0.55,
    "sulaimaniya": 0.5, "al sulaimaniya": 0.5,
    "al wurud": 0.5,
    "al mohammadiyah": 0.45,
    "exit 5": 0.4, "exit 10": 0.45, "exit 15": 0.5,
    "al diriyah": 0.6, "diriyah": 0.6,
    
    # Jeddah - Premium Areas
    "jeddah": 0.35,
    "al hamra": 0.55, "hamra": 0.55,
    "al rawdah": 0.55, "rawdah": 0.55,
    "al shati": 0.6, "shati": 0.6,
    "obhur": 0.5, "abhur": 0.5,
    "al andalus": 0.5,
    "al zahra": 0.5,
    "al salamah": 0.5,
    "al muhammadiyah": 0.45,
    "al khalidiyah": 0.45,
    "al corniche": 0.5,
    "downtown jeddah": 0.4,
    "al balad": 0.35,  # Historic area, mixed income
    
    # Dammam/Eastern Province
    "dammam": 0.35,
    "al khobar": 0.5, "khobar": 0.5,
    "dhahran": 0.55,  # Aramco HQ
    "jubail": 0.45,
    "half moon bay": 0.5,
    
    # Other Saudi
    "mecca": 0.35, "makkah": 0.35,
    "medina": 0.35, "madinah": 0.35,
    "neom": 0.7,  # New mega city project
    "yanbu": 0.4,
    "tabuk": 0.3,
    "abha": 0.3,
    "khamis mushait": 0.25,
    
    # ========== OTHER GCC ==========
    # Qatar
    "doha": 0.5,
    "the pearl": 0.75, "pearl qatar": 0.75,
    "west bay": 0.7,
    "lusail": 0.65,
    "al sadd": 0.5,
    "qatar financial centre": 0.7, "qfc": 0.7,
    
    # Kuwait
    "kuwait city": 0.45,
    "salmiya": 0.5,
    "hawally": 0.4,
    "al kuwait": 0.5,
    "sharq": 0.55,
    
    # Bahrain
    "manama": 0.45,
    "bahrain financial harbour": 0.65,
    "seef": 0.55,
    "amwaj islands": 0.6,
    
    # Oman
    "muscat": 0.4,
    "al mouj": 0.55,
    "qurum": 0.5,
    "shatti al qurum": 0.55,
    
    # ========== INDIA - TECH HUBS ==========
    "bangalore": 0.6, "bengaluru": 0.6,
    "hyderabad": 0.5, "gurugram": 0.6, "gurgaon": 0.6,
    "noida": 0.5, "pune": 0.45, "mumbai": 0.5,
    "chennai": 0.4, "delhi": 0.4, "new delhi": 0.4,
    
    # India - Affluent neighborhoods
    "koramangala": 0.7, "indiranagar": 0.7, "hsr layout": 0.65,
    "whitefield": 0.6, "electronic city": 0.55,
    "bandra": 0.6, "powai": 0.55, "andheri": 0.45,
    "gachibowli": 0.55, "hitec city": 0.55, "hitech city": 0.55,
    "cyber city": 0.6, "dlf": 0.6,
    "jubilee hills": 0.6, "banjara hills": 0.6,
    "defence colony": 0.55, "greater kailash": 0.55, "gk": 0.5,
    "vasant kunj": 0.5, "vasant vihar": 0.55,
    "golf links": 0.7, "jor bagh": 0.65,
    "south mumbai": 0.65, "colaba": 0.6, "cuffe parade": 0.65,
    "worli": 0.6, "lower parel": 0.55,
    "juhu": 0.6, "lokhandwala": 0.5,
    "boat club": 0.6, "adyar": 0.5, "nungambakkam": 0.55,  # Chennai
    "koregaon park": 0.55, "kalyani nagar": 0.5,  # Pune
    "sector 17": 0.5, "sector 29": 0.5, "sector 43": 0.55,  # Gurgaon
    
    # ========== BRAZIL ==========
    "são paulo": 0.4, "sao paulo": 0.4,
    "jardins": 0.6, "itaim bibi": 0.6, "pinheiros": 0.5,
    "vila olimpia": 0.55, "moema": 0.55, "brooklin": 0.5,
    "rio de janeiro": 0.35, "leblon": 0.6, "ipanema": 0.55,
    "copacabana": 0.45, "barra da tijuca": 0.5,
    "brasilia": 0.4, "florianopolis": 0.35,
    
    # ========== MEXICO ==========
    "mexico city": 0.35, "polanco": 0.6, "santa fe": 0.55,
    "condesa": 0.5, "roma norte": 0.5, "roma": 0.45,
    "lomas de chapultepec": 0.6,
    "monterrey": 0.4, "san pedro garza garcia": 0.6,
    "guadalajara": 0.3, "zapopan": 0.4,
    "cancun": 0.35, "playa del carmen": 0.4,
    
    # ========== SOUTHEAST ASIA ==========
    "singapore": 0.3,
    "orchard": 0.5, "marina bay": 0.55, "sentosa": 0.5,
    "hong kong": 0.25,
    "central": 0.5, "the peak": 0.6, "mid levels": 0.5,
    "jakarta": 0.4, "jakarta selatan": 0.5,
    "scbd": 0.55, "sudirman": 0.5, "kuningan": 0.45,
    "menteng": 0.5, "kemang": 0.5,
    "kuala lumpur": 0.35, "bangsar": 0.5, "mont kiara": 0.5,
    "klcc": 0.5, "bukit bintang": 0.45,
    "bangkok": 0.35, "sukhumvit": 0.5, "silom": 0.45,
    "thonglor": 0.55, "ekkamai": 0.5, "sathorn": 0.5,
    "ho chi minh": 0.35, "district 1": 0.5, "district 2": 0.5, "district 7": 0.5,
    "manila": 0.35, "makati": 0.5, "bgc": 0.55, "bonifacio global city": 0.55,
    "rockwell": 0.55, "alabang": 0.45,
    
    # ========== CHINA ==========
    "shanghai": 0.5, "pudong": 0.6, "lujiazui": 0.7,
    "jing'an": 0.6, "xuhui": 0.55, "french concession": 0.6,
    "beijing": 0.5, "chaoyang": 0.55, "zhongguancun": 0.6,
    "wangfujing": 0.55, "sanlitun": 0.55,
    "shenzhen": 0.55, "nanshan": 0.6, "futian": 0.55,
    "guangzhou": 0.4, "tianhe": 0.5,
    "hangzhou": 0.45,
    
    # ========== AFRICA ==========
    "lagos": 0.4, "victoria island": 0.6, "ikoyi": 0.6, "lekki": 0.55,
    "banana island": 0.7,
    "nairobi": 0.35, "westlands": 0.5, "karen": 0.55,
    "gigiri": 0.55, "lavington": 0.5,
    "johannesburg": 0.4, "sandton": 0.6, "rosebank": 0.5,
    "hyde park": 0.55, "fourways": 0.45,
    "cape town": 0.4, "camps bay": 0.6, "constantia": 0.6,
    "clifton": 0.65, "waterfront": 0.55,
    "cairo": 0.35, "zamalek": 0.5, "maadi": 0.45,
    "new cairo": 0.5, "5th settlement": 0.5, "sheikh zayed": 0.45,
    
    # ========== EASTERN EUROPE ==========
    "warsaw": 0.35, "krakow": 0.3,
    "prague": 0.35,
    "bucharest": 0.3,
    "budapest": 0.3,
    "kyiv": 0.3, "kiev": 0.3,
    "tallinn": 0.4,
    
    # ========== LATIN AMERICA ==========
    "buenos aires": 0.35, "palermo": 0.5, "recoleta": 0.5,
    "puerto madero": 0.55,
    "santiago": 0.4, "las condes": 0.55, "vitacura": 0.6,
    "providencia": 0.5,
    "bogota": 0.35, "chapinero": 0.45, "usaquen": 0.5,
    "lima": 0.3, "miraflores": 0.5, "san isidro": 0.55,
    
    # ========== TURKEY ==========
    "istanbul": 0.35, "besiktas": 0.5, "sisli": 0.45, "kadikoy": 0.4,
    "nisantasi": 0.55, "bebek": 0.55, "etiler": 0.5,
    "levent": 0.5, "maslak": 0.5,
    
    # ========== RUSSIA ==========
    "moscow": 0.45, "saint petersburg": 0.35,
}

# ========== SUNK COST MULTIPLIERS ==========
# Users who have invested more time/effort are more likely to pay
# This is applied as a multiplier to the final price (1.0 = no change)

def calculate_sunk_cost_multiplier(engagement_data: dict) -> float:
    """
    Calculate price adjustment based on user's sunk cost / engagement + device.
    Higher engagement = user is more invested = higher willingness to pay
    Premium device = higher willingness to pay
    
    Returns multiplier between 0.8 (low engagement) and 1.4 (high engagement + premium device)
    """
    edits = engagement_data.get("edit_count", 0)
    pages = engagement_data.get("page_count", 1)
    time_spent = engagement_data.get("time_spent_seconds", 0)
    interactions = engagement_data.get("interaction_count", 0)
    has_added_images = engagement_data.get("has_added_images", False)
    has_signature = engagement_data.get("has_signature", False)
    has_drawings = engagement_data.get("has_drawings", False)
    
    # Device info
    is_premium_device = engagement_data.get("is_premium_device", False)
    device_type = engagement_data.get("device_type", "desktop")
    os = engagement_data.get("os", "unknown")
    browser = engagement_data.get("browser", "unknown")
    
    multiplier = 1.0
    
    # Edit count contribution (0 to +0.15)
    if edits >= 10:
        multiplier += 0.15
    elif edits >= 5:
        multiplier += 0.10
    elif edits >= 2:
        multiplier += 0.05
    
    # Page count contribution - more pages = more work (0 to +0.10)
    if pages >= 20:
        multiplier += 0.10
    elif pages >= 10:
        multiplier += 0.07
    elif pages >= 5:
        multiplier += 0.05
    
    # Time spent contribution (0 to +0.15)
    minutes = time_spent / 60
    if minutes >= 10:
        multiplier += 0.15
    elif minutes >= 5:
        multiplier += 0.10
    elif minutes >= 2:
        multiplier += 0.05
    
    # Rich content additions (each +0.05)
    if has_added_images:
        multiplier += 0.05
    if has_signature:
        multiplier += 0.05
    if has_drawings:
        multiplier += 0.05
    
    # Interaction count - tool switches, format changes, etc. (0 to +0.10)
    if interactions >= 30:
        multiplier += 0.10
    elif interactions >= 15:
        multiplier += 0.07
    elif interactions >= 5:
        multiplier += 0.03
    
    # ===== DEVICE-BASED ADJUSTMENTS =====
    # Premium device boost (iOS, macOS, flagship Android)
    if is_premium_device:
        multiplier += 0.10
    
    # OS-based adjustments
    if os == "ios":
        multiplier += 0.05  # iOS users have higher conversion rates
    elif os == "macos":
        multiplier += 0.05  # Mac users typically higher value
    
    # Browser-based adjustments (Safari users on Apple = premium)
    if browser == "safari" and os in ["ios", "macos"]:
        multiplier += 0.03
    
    # Low engagement discount (encourage conversion)
    if edits == 0 and time_spent < 60:
        multiplier = 0.85  # 15% discount for quick users
    elif edits <= 1 and time_spent < 120:
        multiplier = 0.9   # 10% discount for light users
    
    # Cap the multiplier
    return max(0.8, min(1.4, multiplier))

# Country pricing data: currency, multiplier (PPP-adjusted), rounded price, stripe currency code
# Multiplier: 1.0 = full price (~$3), 0.1 = lowest price (~$0.50)
COUNTRY_PRICING = {
    # High income - full price
    "US": {"currency": "usd", "symbol": "$", "multiplier": 1.0, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "GB": {"currency": "gbp", "symbol": "£", "multiplier": 0.85, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "DE": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "FR": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "AU": {"currency": "aud", "symbol": "A$", "multiplier": 1.1, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "CA": {"currency": "cad", "symbol": "C$", "multiplier": 1.0, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "JP": {"currency": "jpy", "symbol": "¥", "multiplier": 0.9, "round_to": 0, "min_price": 300, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "CH": {"currency": "chf", "symbol": "CHF", "multiplier": 1.1, "round_to": 90, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SG": {"currency": "sgd", "symbol": "S$", "multiplier": 0.95, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NL": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SE": {"currency": "sek", "symbol": "kr", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NO": {"currency": "nok", "symbol": "kr", "multiplier": 0.95, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "DK": {"currency": "dkk", "symbol": "kr", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NZ": {"currency": "nzd", "symbol": "NZ$", "multiplier": 0.95, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "IE": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "HK": {"currency": "hkd", "symbol": "HK$", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    
    # GCC - Premium markets (UAE, Saudi, Qatar, Kuwait, Bahrain, Oman)
    "AE": {"currency": "aed", "symbol": "د.إ", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SA": {"currency": "sar", "symbol": "ر.س", "multiplier": 0.75, "round_to": 0, "payment_methods": ["card", "apple_pay", "mada"]},
    "QA": {"currency": "qar", "symbol": "ر.ق", "multiplier": 0.9, "round_to": 0, "payment_methods": ["card", "apple_pay"]},
    "KW": {"currency": "kwd", "symbol": "د.ك", "multiplier": 0.95, "round_to": 0, "payment_methods": ["card"]},
    "BH": {"currency": "bhd", "symbol": ".د.ب", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card"]},
    "OM": {"currency": "omr", "symbol": "ر.ع.", "multiplier": 0.8, "round_to": 0, "payment_methods": ["card"]},
    
    # Medium income - moderate prices
    "ES": {"currency": "eur", "symbol": "€", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "IT": {"currency": "eur", "symbol": "€", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "PT": {"currency": "eur", "symbol": "€", "multiplier": 0.6, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "KR": {"currency": "krw", "symbol": "₩", "multiplier": 0.7, "round_to": 0, "min_price": 2000, "payment_methods": ["card"]},
    "PL": {"currency": "pln", "symbol": "zł", "multiplier": 0.5, "round_to": 99, "payment_methods": ["card"]},
    "CZ": {"currency": "czk", "symbol": "Kč", "multiplier": 0.5, "round_to": 0, "payment_methods": ["card"]},
    "GR": {"currency": "eur", "symbol": "€", "multiplier": 0.55, "round_to": 99, "payment_methods": ["card"]},
    "IL": {"currency": "ils", "symbol": "₪", "multiplier": 0.75, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "TW": {"currency": "twd", "symbol": "NT$", "multiplier": 0.6, "round_to": 0, "payment_methods": ["card"]},
    "CL": {"currency": "clp", "symbol": "$", "multiplier": 0.45, "round_to": 0, "min_price": 1000, "payment_methods": ["card"]},
    "CN": {"currency": "cny", "symbol": "¥", "multiplier": 0.5, "round_to": 0, "payment_methods": ["card"]},
    
    # Lower-middle income - lower prices
    "MX": {"currency": "mxn", "symbol": "$", "multiplier": 0.35, "round_to": 0, "payment_methods": ["card"]},
    "BR": {"currency": "brl", "symbol": "R$", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "AR": {"currency": "ars", "symbol": "$", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card"]},
    "CO": {"currency": "cop", "symbol": "$", "multiplier": 0.25, "round_to": 0, "min_price": 5000, "payment_methods": ["card"]},
    "TR": {"currency": "try", "symbol": "₺", "multiplier": 0.25, "round_to": 99, "payment_methods": ["card"]},
    "TH": {"currency": "thb", "symbol": "฿", "multiplier": 0.3, "round_to": 0, "payment_methods": ["card"]},
    "MY": {"currency": "myr", "symbol": "RM", "multiplier": 0.35, "round_to": 99, "payment_methods": ["card", "grabpay"]},
    "ZA": {"currency": "zar", "symbol": "R", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "RO": {"currency": "ron", "symbol": "lei", "multiplier": 0.4, "round_to": 99, "payment_methods": ["card"]},
    "HU": {"currency": "huf", "symbol": "Ft", "multiplier": 0.4, "round_to": 0, "min_price": 500, "payment_methods": ["card"]},
    "PE": {"currency": "pen", "symbol": "S/", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "RU": {"currency": "rub", "symbol": "₽", "multiplier": 0.3, "round_to": 0, "payment_methods": ["card"]},
    
    # Lower income - lowest prices for maximum accessibility
    "IN": {"currency": "inr", "symbol": "₹", "multiplier": 0.15, "round_to": 0, "min_price": 49, "payment_methods": ["card", "upi"]},
    "ID": {"currency": "idr", "symbol": "Rp", "multiplier": 0.15, "round_to": 0, "min_price": 15000, "payment_methods": ["card"]},
    "PH": {"currency": "php", "symbol": "₱", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card", "grabpay"]},
    "VN": {"currency": "vnd", "symbol": "₫", "multiplier": 0.12, "round_to": 0, "min_price": 25000, "payment_methods": ["card"]},
    "PK": {"currency": "pkr", "symbol": "Rs", "multiplier": 0.1, "round_to": 0, "min_price": 200, "payment_methods": ["card"]},
    "BD": {"currency": "bdt", "symbol": "৳", "multiplier": 0.1, "round_to": 0, "min_price": 99, "payment_methods": ["card"]},
    "NG": {"currency": "ngn", "symbol": "₦", "multiplier": 0.1, "round_to": 0, "min_price": 500, "payment_methods": ["card"]},
    "EG": {"currency": "egp", "symbol": "E£", "multiplier": 0.15, "round_to": 0, "payment_methods": ["card"]},
    "KE": {"currency": "kes", "symbol": "KSh", "multiplier": 0.12, "round_to": 0, "payment_methods": ["card"]},
    "UA": {"currency": "uah", "symbol": "₴", "multiplier": 0.15, "round_to": 0, "payment_methods": ["card"]},
    "LK": {"currency": "lkr", "symbol": "Rs", "multiplier": 0.12, "round_to": 0, "min_price": 300, "payment_methods": ["card"]},
    "NP": {"currency": "npr", "symbol": "Rs", "multiplier": 0.1, "round_to": 0, "min_price": 150, "payment_methods": ["card"]},
    "GH": {"currency": "ghs", "symbol": "GH₵", "multiplier": 0.12, "round_to": 99, "payment_methods": ["card"]},
    "MA": {"currency": "mad", "symbol": "DH", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card"]},
}

# Default for unknown countries
DEFAULT_PRICING = {"currency": "usd", "symbol": "$", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]}

# Approximate exchange rates (updated periodically)
EXCHANGE_RATES = {
    "usd": 1.0, "eur": 0.92, "gbp": 0.79, "jpy": 149.5, "aud": 1.53, "cad": 1.36,
    "chf": 0.88, "cny": 7.24, "inr": 83.1, "mxn": 17.15, "brl": 4.97, "krw": 1320,
    "sgd": 1.34, "hkd": 7.82, "sek": 10.42, "nok": 10.58, "dkk": 6.87, "nzd": 1.63,
    "zar": 18.65, "rub": 91.5, "try": 30.2, "pln": 3.98, "thb": 35.5, "idr": 15650,
    "myr": 4.72, "php": 55.8, "vnd": 24500, "aed": 3.67, "sar": 3.75, "egp": 30.9,
    "pkr": 278, "bdt": 110, "ngn": 1250, "kes": 153, "cop": 3950, "clp": 878,
    "pen": 3.72, "ars": 815, "uah": 37.5, "ron": 4.57, "huf": 355, "czk": 22.7,
    "ils": 3.65, "qar": 3.64, "kwd": 0.31, "twd": 31.5, "lkr": 325, "npr": 133,
    "ghs": 12.3, "mad": 10.1, "bhd": 0.376, "omr": 0.385
}

# Country pricing data: currency, multiplier (PPP-adjusted), rounded price, stripe currency code
# Multiplier: 1.0 = full price (~$3), 0.1 = lowest price (~$0.50)
COUNTRY_PRICING = {
    # High income - full price
    "US": {"currency": "usd", "symbol": "$", "multiplier": 1.0, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "GB": {"currency": "gbp", "symbol": "£", "multiplier": 0.85, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "DE": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "FR": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "AU": {"currency": "aud", "symbol": "A$", "multiplier": 1.1, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "CA": {"currency": "cad", "symbol": "C$", "multiplier": 1.0, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "JP": {"currency": "jpy", "symbol": "¥", "multiplier": 0.9, "round_to": 0, "min_price": 300, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "CH": {"currency": "chf", "symbol": "CHF", "multiplier": 1.1, "round_to": 90, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SG": {"currency": "sgd", "symbol": "S$", "multiplier": 0.95, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "AE": {"currency": "aed", "symbol": "د.إ", "multiplier": 0.9, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NL": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SE": {"currency": "sek", "symbol": "kr", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NO": {"currency": "nok", "symbol": "kr", "multiplier": 0.95, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "DK": {"currency": "dkk", "symbol": "kr", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "NZ": {"currency": "nzd", "symbol": "NZ$", "multiplier": 0.95, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "IE": {"currency": "eur", "symbol": "€", "multiplier": 0.9, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "HK": {"currency": "hkd", "symbol": "HK$", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    
    # Medium income - moderate prices
    "ES": {"currency": "eur", "symbol": "€", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "IT": {"currency": "eur", "symbol": "€", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "PT": {"currency": "eur", "symbol": "€", "multiplier": 0.6, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "KR": {"currency": "krw", "symbol": "₩", "multiplier": 0.7, "round_to": 0, "min_price": 2000, "payment_methods": ["card"]},
    "PL": {"currency": "pln", "symbol": "zł", "multiplier": 0.5, "round_to": 99, "payment_methods": ["card"]},
    "CZ": {"currency": "czk", "symbol": "Kč", "multiplier": 0.5, "round_to": 0, "payment_methods": ["card"]},
    "GR": {"currency": "eur", "symbol": "€", "multiplier": 0.55, "round_to": 99, "payment_methods": ["card"]},
    "IL": {"currency": "ils", "symbol": "₪", "multiplier": 0.75, "round_to": 0, "payment_methods": ["card", "apple_pay", "google_pay"]},
    "SA": {"currency": "sar", "symbol": "ر.س", "multiplier": 0.7, "round_to": 0, "payment_methods": ["card", "apple_pay"]},
    "QA": {"currency": "qar", "symbol": "ر.ق", "multiplier": 0.85, "round_to": 0, "payment_methods": ["card"]},
    "KW": {"currency": "kwd", "symbol": "د.ك", "multiplier": 0.9, "round_to": 0, "payment_methods": ["card"]},
    "TW": {"currency": "twd", "symbol": "NT$", "multiplier": 0.6, "round_to": 0, "payment_methods": ["card"]},
    "CL": {"currency": "clp", "symbol": "$", "multiplier": 0.45, "round_to": 0, "min_price": 1000, "payment_methods": ["card"]},
    "CN": {"currency": "cny", "symbol": "¥", "multiplier": 0.5, "round_to": 0, "payment_methods": ["card"]},
    
    # Lower-middle income - lower prices
    "MX": {"currency": "mxn", "symbol": "$", "multiplier": 0.35, "round_to": 0, "payment_methods": ["card"]},
    "BR": {"currency": "brl", "symbol": "R$", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "AR": {"currency": "ars", "symbol": "$", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card"]},
    "CO": {"currency": "cop", "symbol": "$", "multiplier": 0.25, "round_to": 0, "min_price": 5000, "payment_methods": ["card"]},
    "TR": {"currency": "try", "symbol": "₺", "multiplier": 0.25, "round_to": 99, "payment_methods": ["card"]},
    "TH": {"currency": "thb", "symbol": "฿", "multiplier": 0.3, "round_to": 0, "payment_methods": ["card"]},
    "MY": {"currency": "myr", "symbol": "RM", "multiplier": 0.35, "round_to": 99, "payment_methods": ["card", "grabpay"]},
    "ZA": {"currency": "zar", "symbol": "R", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "RO": {"currency": "ron", "symbol": "lei", "multiplier": 0.4, "round_to": 99, "payment_methods": ["card"]},
    "HU": {"currency": "huf", "symbol": "Ft", "multiplier": 0.4, "round_to": 0, "min_price": 500, "payment_methods": ["card"]},
    "PE": {"currency": "pen", "symbol": "S/", "multiplier": 0.3, "round_to": 99, "payment_methods": ["card"]},
    "RU": {"currency": "rub", "symbol": "₽", "multiplier": 0.3, "round_to": 0, "payment_methods": ["card"]},
    
    # Lower income - lowest prices for maximum accessibility
    "IN": {"currency": "inr", "symbol": "₹", "multiplier": 0.15, "round_to": 0, "min_price": 49, "payment_methods": ["card", "upi"]},
    "ID": {"currency": "idr", "symbol": "Rp", "multiplier": 0.15, "round_to": 0, "min_price": 15000, "payment_methods": ["card"]},
    "PH": {"currency": "php", "symbol": "₱", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card", "grabpay"]},
    "VN": {"currency": "vnd", "symbol": "₫", "multiplier": 0.12, "round_to": 0, "min_price": 25000, "payment_methods": ["card"]},
    "PK": {"currency": "pkr", "symbol": "Rs", "multiplier": 0.1, "round_to": 0, "min_price": 200, "payment_methods": ["card"]},
    "BD": {"currency": "bdt", "symbol": "৳", "multiplier": 0.1, "round_to": 0, "min_price": 99, "payment_methods": ["card"]},
    "NG": {"currency": "ngn", "symbol": "₦", "multiplier": 0.1, "round_to": 0, "min_price": 500, "payment_methods": ["card"]},
    "EG": {"currency": "egp", "symbol": "E£", "multiplier": 0.15, "round_to": 0, "payment_methods": ["card"]},
    "KE": {"currency": "kes", "symbol": "KSh", "multiplier": 0.12, "round_to": 0, "payment_methods": ["card"]},
    "UA": {"currency": "uah", "symbol": "₴", "multiplier": 0.15, "round_to": 0, "payment_methods": ["card"]},
    "LK": {"currency": "lkr", "symbol": "Rs", "multiplier": 0.12, "round_to": 0, "min_price": 300, "payment_methods": ["card"]},
    "NP": {"currency": "npr", "symbol": "Rs", "multiplier": 0.1, "round_to": 0, "min_price": 150, "payment_methods": ["card"]},
    "GH": {"currency": "ghs", "symbol": "GH₵", "multiplier": 0.12, "round_to": 99, "payment_methods": ["card"]},
    "MA": {"currency": "mad", "symbol": "DH", "multiplier": 0.2, "round_to": 0, "payment_methods": ["card"]},
}

# Default for unknown countries
DEFAULT_PRICING = {"currency": "usd", "symbol": "$", "multiplier": 0.7, "round_to": 99, "payment_methods": ["card", "apple_pay", "google_pay"]}

# Approximate exchange rates (updated periodically)
EXCHANGE_RATES = {
    "usd": 1.0, "eur": 0.92, "gbp": 0.79, "jpy": 149.5, "aud": 1.53, "cad": 1.36,
    "chf": 0.88, "cny": 7.24, "inr": 83.1, "mxn": 17.15, "brl": 4.97, "krw": 1320,
    "sgd": 1.34, "hkd": 7.82, "sek": 10.42, "nok": 10.58, "dkk": 6.87, "nzd": 1.63,
    "zar": 18.65, "rub": 91.5, "try": 30.2, "pln": 3.98, "thb": 35.5, "idr": 15650,
    "myr": 4.72, "php": 55.8, "vnd": 24500, "aed": 3.67, "sar": 3.75, "egp": 30.9,
    "pkr": 278, "bdt": 110, "ngn": 1250, "kes": 153, "cop": 3950, "clp": 878,
    "pen": 3.72, "ars": 815, "uah": 37.5, "ron": 4.57, "huf": 355, "czk": 22.7,
    "ils": 3.65, "qar": 3.64, "kwd": 0.31, "twd": 31.5, "lkr": 325, "npr": 133,
    "ghs": 12.3, "mad": 10.1
}

def get_geo_from_ip(ip: str) -> dict:
    """Get geolocation from IP using free API - includes city-level data"""
    try:
        # Try ip-api.com (free, no key needed, returns city data)
        response = httpx.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,region,regionName,city,district,zip,lat,lon,isp,org",
            timeout=3.0
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                return {
                    "country": data.get("country", "Unknown"),
                    "country_code": data.get("countryCode", "US"),
                    "region": data.get("regionName", ""),
                    "city": data.get("city", ""),
                    "district": data.get("district", ""),  # Neighborhood level
                    "zip": data.get("zip", ""),
                    "isp": data.get("isp", ""),
                    "org": data.get("org", ""),  # Often shows company name
                    "lat": data.get("lat", 0),
                    "lon": data.get("lon", 0)
                }
    except:
        pass
    return {"country": "Unknown", "country_code": "US", "region": "", "city": "", "district": "", "zip": "", "isp": "", "org": ""}

def get_city_boost(geo_info: dict) -> float:
    """
    Calculate price multiplier boost based on city/neighborhood.
    Returns a boost value to ADD to the country multiplier.
    """
    city = geo_info.get("city", "").lower().strip()
    district = geo_info.get("district", "").lower().strip()
    region = geo_info.get("region", "").lower().strip()
    org = geo_info.get("org", "").lower().strip()
    
    boost = 0.0
    
    # Check district first (most specific - e.g., Koramangala, Indiranagar)
    if district:
        for loc, loc_boost in HIGH_VALUE_CITIES.items():
            if loc in district:
                boost = max(boost, loc_boost)
    
    # Check city
    if city:
        for loc, loc_boost in HIGH_VALUE_CITIES.items():
            if loc in city or city in loc:
                boost = max(boost, loc_boost)
    
    # Check region (for places like "Karnataka" which contains Bangalore)
    if region and boost == 0:
        # Only use region if no city match, and be more conservative
        for loc, loc_boost in HIGH_VALUE_CITIES.items():
            if loc in region:
                boost = max(boost, loc_boost * 0.5)  # Half boost for region-only match
    
    # Additional boost for tech company networks (detected via org/ISP)
    tech_indicators = [
        "google", "microsoft", "amazon", "meta", "facebook", "apple", 
        "infosys", "wipro", "tcs", "cognizant", "accenture",
        "flipkart", "swiggy", "zomato", "ola", "paytm", "razorpay",
        "grab", "gojek", "sea limited", "shopee",
        "reliance jio", "jio",
    ]
    
    if org:
        for tech in tech_indicators:
            if tech in org:
                boost = max(boost, 0.3)  # Minimum 0.3 boost for tech company employees
                break
    
    # Cap the boost so we don't go too crazy
    return min(boost, 0.7)

def calculate_price(country_code: str, geo_info: dict = None, engagement_data: dict = None) -> dict:
    """Calculate localized price based on country, city/neighborhood, AND user engagement"""
    pricing = COUNTRY_PRICING.get(country_code.upper(), DEFAULT_PRICING)
    
    # Layer 1: Base multiplier from country
    base_multiplier = pricing["multiplier"]
    
    # Layer 2: City-level boost if available
    city_boost = 0.0
    if geo_info:
        city_boost = get_city_boost(geo_info)
    
    # Layer 3: Sunk cost multiplier based on engagement
    sunk_cost_mult = 1.0
    if engagement_data:
        sunk_cost_mult = calculate_sunk_cost_multiplier(engagement_data)
    
    # Combine: (country + city boost) * sunk cost, capped at 1.0 for base, then sunk cost can push up to 1.3
    geo_multiplier = min(base_multiplier + city_boost, 1.0)
    final_multiplier = geo_multiplier * sunk_cost_mult
    
    # Calculate base price in local currency
    currency = pricing["currency"]
    exchange_rate = EXCHANGE_RATES.get(currency, 1.0)
    
    # Base calculation: USD cents * multiplier * exchange rate
    base_amount = BASE_PRICE_USD_CENTS * final_multiplier * exchange_rate / 100
    
    # Apply minimum price if set
    min_price = pricing.get("min_price", 0)
    if min_price > 0:
        base_amount = max(base_amount, min_price / 100)
    
    # Round to local convention
    round_to = pricing["round_to"]
    if round_to == 0:
        # Round to whole number
        if currency in ["jpy", "krw", "vnd", "idr", "cop", "clp", "huf"]:
            # Round to nearest 100 or 1000 for these currencies
            if base_amount > 1000:
                final_amount = round(base_amount / 100) * 100
            else:
                final_amount = round(base_amount / 10) * 10
        else:
            final_amount = round(base_amount)
    elif round_to == 99:
        # Round to .99
        final_amount = math.floor(base_amount) + 0.99
        if final_amount < 1:
            final_amount = 0.99
    elif round_to == 90:
        # Round to .90
        final_amount = math.floor(base_amount) + 0.90
    else:
        final_amount = round(base_amount, 2)
    
    # Ensure minimum $0.50 equivalent
    min_usd_equivalent = 0.50
    min_local = min_usd_equivalent * exchange_rate
    if final_amount < min_local:
        final_amount = min_local
    
    # Convert to cents/smallest unit for Stripe
    if currency in ["jpy", "krw", "vnd", "idr"]:
        stripe_amount = int(final_amount)  # These don't have decimals
    else:
        stripe_amount = int(final_amount * 100)
    
    # Format display price
    if currency in ["jpy", "krw", "vnd", "idr", "cop", "clp", "huf"]:
        display_price = f"{pricing['symbol']}{int(final_amount):,}"
    elif currency in ["eur", "chf", "pln", "czk", "ron", "sek", "nok", "dkk"]:
        display_price = f"{final_amount:.2f} {pricing['symbol']}"
    else:
        display_price = f"{pricing['symbol']}{final_amount:.2f}"
    
    return {
        "amount": stripe_amount,
        "currency": currency,
        "display_price": display_price,
        "symbol": pricing["symbol"],
        "payment_methods": pricing["payment_methods"],
        "country_code": country_code.upper(),
        "city_boost_applied": city_boost > 0,
        "sunk_cost_multiplier": sunk_cost_mult,
        "final_multiplier": final_multiplier
    }

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
    
    # Extract text and build response - OPTIMIZED
    fonts_used, sizes_used, colors_used = {}, {}, {}
    pages_data = []
    
    # Use lower resolution for faster loading (1.5 instead of 2.5)
    # This significantly speeds up rendering while maintaining good quality
    scale = 1.8
    
    for pn in range(len(doc)):
        page = doc[pn]
        
        # Render page to image with optimized settings
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        
        # Convert to JPEG for smaller file size (faster transfer)
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

# ============ PRICING & PAYMENTS ============
@app.get("/api/stripe-config")
async def get_stripe_config():
    """Return Stripe publishable key (safe to expose)"""
    pk = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    return {"publishable_key": pk, "enabled": STRIPE_ENABLED and bool(pk)}

@app.get("/api/pricing")
async def get_pricing(request: Request):
    """Get localized pricing for the user based on country AND city (preview price)"""
    ip = request.client.host if request.client else "0.0.0.0"
    
    # Try to get country from headers first (CDN/proxy)
    country_code = (
        request.headers.get("cf-ipcountry") or 
        request.headers.get("x-vercel-ip-country") or
        request.headers.get("x-country-code") or
        None
    )
    
    # Always do IP lookup for city-level data
    geo_info = get_geo_from_ip(ip)
    
    if not country_code or country_code == "XX":
        country_code = geo_info.get("country_code", "US")
    
    # Calculate preview price (no engagement data yet - just geo)
    pricing = calculate_price(country_code, geo_info, None)
    pricing["geo"] = {
        "country": geo_info.get("country", "Unknown"),
        "city": geo_info.get("city", ""),
        "region": geo_info.get("region", "")
    }
    
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
    # Device info
    device_type: str = Form("desktop"),
    os: str = Form("unknown"),
    browser: str = Form("unknown"),
    is_premium_device: bool = Form(False),
    can_apple_pay: bool = Form(False),
    can_google_pay: bool = Form(True)
):
    """Create a Stripe Payment Intent with city-aware + sunk cost + device pricing"""
    if not STRIPE_ENABLED:
        raise HTTPException(500, "Payments not configured")
    
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    ip = request.client.host if request.client else "0.0.0.0"
    
    # Get full geo info for city-level pricing
    geo_info = get_geo_from_ip(ip)
    country_code = (
        request.headers.get("cf-ipcountry") or 
        request.headers.get("x-vercel-ip-country") or
        geo_info.get("country_code", "US")
    )
    
    # Build engagement data for sunk cost calculation (includes device info)
    engagement_data = {
        "edit_count": edit_count,
        "page_count": page_count,
        "time_spent_seconds": time_spent_seconds,
        "interaction_count": interaction_count,
        "has_added_images": has_added_images,
        "has_signature": has_signature,
        "has_drawings": has_drawings,
        # Device info
        "device_type": device_type,
        "os": os,
        "browser": browser,
        "is_premium_device": is_premium_device
    }
    
    # Calculate price with all layers: country + city + sunk cost + device
    pricing = calculate_price(country_code, geo_info, engagement_data)
    
    # Filter payment methods based on device capabilities
    available_methods = []
    for method in pricing["payment_methods"]:
        if method == "apple_pay" and not can_apple_pay:
            continue
        if method == "google_pay" and not can_google_pay:
            continue
        available_methods.append(method)
    
    # Ensure card is always available
    if "card" not in available_methods:
        available_methods.insert(0, "card")
    
    try:
        # Create Payment Intent
        intent = stripe.PaymentIntent.create(
            amount=pricing["amount"],
            currency=pricing["currency"],
            metadata={
                "pdf_id": pdf_id,
                "country": country_code,
                "city": geo_info.get("city", ""),
                "ip": ip,
                "city_boost": str(pricing.get("city_boost_applied", False)),
                "sunk_cost_mult": str(pricing.get("sunk_cost_multiplier", 1.0)),
                "edits": str(edit_count),
                "time_spent": str(time_spent_seconds),
                "device": device_type,
                "os": os,
                "browser": browser,
                "premium_device": str(is_premium_device)
            },
            automatic_payment_methods={"enabled": True}
        )
        
        return {
            "clientSecret": intent.client_secret,
            "paymentIntentId": intent.id,
            "amount": pricing["amount"],
            "currency": pricing["currency"],
            "display_price": pricing["display_price"],
            "payment_methods": available_methods
        }
    except Exception as e:
        raise HTTPException(500, f"Payment error: {str(e)}")

@app.post("/api/verify-payment")
async def verify_payment(request: Request, payment_intent_id: str = Form(...)):
    """Verify a payment was successful"""
    if not STRIPE_ENABLED:
        raise HTTPException(500, "Payments not configured")
    
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        if intent.status == "succeeded":
            pdf_id = intent.metadata.get("pdf_id")
            
            # Track the sale
            track_action(request, "Purchase", f"${intent.amount/100:.2f} {intent.currency.upper()}")
            
            return {
                "success": True,
                "pdf_id": pdf_id,
                "download_token": base64.b64encode(f"{pdf_id}:{payment_intent_id}".encode()).decode()
            }
        else:
            return {"success": False, "status": intent.status}
    except Exception as e:
        raise HTTPException(500, f"Verification error: {str(e)}")

@app.get("/download/{pdf_id}")
async def download_pdf(request: Request, pdf_id: str, token: Optional[str] = None):
    """Download PDF - requires valid payment token"""
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    # Verify payment token
    if token:
        try:
            decoded = base64.b64decode(token).decode()
            token_pdf_id, payment_id = decoded.split(":")
            if token_pdf_id == pdf_id:
                # Valid token - allow download
                s = pdf_storage[pdf_id]
                analytics["total_downloads"] += 1
                track_action(request, "Download", s["filename"])
                
                return StreamingResponse(io.BytesIO(s["content"]), media_type="application/pdf", 
                                        headers={"Content-Disposition": f"attachment; filename={s['filename']}"})
        except:
            pass
    
    raise HTTPException(403, "Payment required")

@app.get("/download-direct/{pdf_id}")
async def download_direct(request: Request, pdf_id: str, token: Optional[str] = None):
    return await download_pdf(request, pdf_id, token)

@app.post("/save-and-download")
async def save_and_download(
    request: Request, 
    pdf_id: str = Form(...), 
    edits: str = Form("[]"),
    token: str = Form(...)
):
    """Save edits and download - requires valid payment token"""
    if pdf_id not in pdf_storage:
        raise HTTPException(404, "PDF not found")
    
    # Verify payment token
    try:
        decoded = base64.b64decode(token).decode()
        token_pdf_id, payment_id = decoded.split(":")
        if token_pdf_id != pdf_id:
            raise HTTPException(403, "Invalid token")
    except:
        raise HTTPException(403, "Payment required")
    
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
