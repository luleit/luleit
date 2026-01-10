from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import os, json, tempfile, base64, stripe, uuid, io, math, zipfile, re
from datetime import datetime, timedelta
from typing import List, Optional
from PIL import Image

# ============ CONFIG ============
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_xxx")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_xxx")
DOMAIN = os.getenv("DOMAIN", "http://localhost:8000")
PRICE_PREMIUM, PRICE_STANDARD, PRICE_BUDGET = 399, 199, 99
PREMIUM_COUNTRIES = ['SA','AE','QA','KW','BH','OM','US','GB','AU','SG','NZ','CA','IE','NO','NL','DK','SE','FI','CH','LU']

stripe.api_key = STRIPE_SECRET_KEY
app = FastAPI(title="Luleit PDF Editor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

pdf_storage = {}
payment_sessions = {}

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

# ============ ROUTES ============
@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html") as f: return f.read()

@app.get("/get-price")
async def get_price(request: Request):
    cores = int(request.query_params.get("cores", 4))
    platform = request.query_params.get("platform", "").lower()
    country = request.headers.get("cf-ipcountry", "US")
    score = (3 if cores >= 8 else 1 if cores >= 4 else 0) + (2 if "mac" in platform else 0) + (3 if country in PREMIUM_COUNTRIES else 0)
    price = PRICE_PREMIUM if score >= 6 else PRICE_STANDARD if score >= 3 else PRICE_BUDGET
    return {"price":price,"display_price":f"${price/100:.2f}","original_price":999}

# ============ UPLOAD & EDIT ============
@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 50*1024*1024: raise HTTPException(400, "File too large")
    try: doc = fitz.open(stream=content, filetype="pdf")
    except: raise HTTPException(400, "Invalid PDF")
    
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

# ============ MERGE ============
@app.post("/merge-pdfs")
async def merge_pdfs(files: List[UploadFile] = File(...)):
    if len(files) < 2: raise HTTPException(400, "Need 2+ files")
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
    pdf_storage[pdf_id] = {"content":content,"filename":"merged.pdf","created":datetime.now()}
    
    return {"pdfId":pdf_id,"pageCount":fitz.open(stream=content,filetype="pdf").page_count,"pages":generate_preview(content),"fileName":"merged.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

# ============ SPLIT ============
@app.post("/split-pdf")
async def split_pdf(pdf_id: str = Form(...), ranges: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    range_list = json.loads(ranges)  # [[1,3], [4,6], [7,10]] or [[1],[2],[3]]
    
    result_files = []
    for i, r in enumerate(range_list):
        new_doc = fitz.open()
        start = r[0] - 1
        end = r[-1] if len(r) > 1 else r[0]
        new_doc.insert_pdf(doc, from_page=start, to_page=end-1)
        
        out = io.BytesIO()
        new_doc.save(out)
        new_doc.close()
        
        split_id = str(uuid.uuid4())
        content = out.getvalue()
        pdf_storage[split_id] = {"content":content,"filename":f"split_{i+1}.pdf","created":datetime.now()}
        result_files.append({"pdfId":split_id,"fileName":f"split_{i+1}.pdf","pageCount":end-start,"fileSize":f"{len(content)/1024:.1f} KB"})
    
    doc.close()
    return {"files":result_files}

# ============ EXTRACT PAGES ============
@app.post("/extract-pages")
async def extract_pages(pdf_id: str = Form(...), pages: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page_list = json.loads(pages)  # [1, 3, 5, 7]
    
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

# ============ COMPRESS ============
@app.post("/compress-pdf")
async def compress_pdf(file: UploadFile = File(...), quality: str = Form("medium")):
    content = await file.read()
    original_size = len(content)
    
    doc = fitz.open(stream=content, filetype="pdf")
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
    pdf_id = str(uuid.uuid4())
    pdf_storage[pdf_id] = {"content":compressed,"filename":f"compressed_{file.filename}","created":datetime.now()}
    
    return {"pdfId":pdf_id,"originalSize":f"{original_size/1024:.1f} KB","compressedSize":f"{len(compressed)/1024:.1f} KB","savings":f"{((original_size-len(compressed))/original_size)*100:.1f}%","fileName":f"compressed_{file.filename}"}

# ============ PAGE OPERATIONS ============
@app.post("/page-operations")
async def page_operations(pdf_id: str = Form(...), operations: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    ops = json.loads(operations)
    
    for op in ops:
        if op["type"] == "reorder":
            new_doc = fitz.open()
            for p in op["order"]: new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)
            doc.close()
            doc = new_doc
    
    for op in ops:
        if op["type"] == "rotate" and 0 <= op["page"]-1 < len(doc):
            doc[op["page"]-1].set_rotation(doc[op["page"]-1].rotation + op["angle"])
    
    for p in sorted([op["page"]-1 for op in ops if op["type"]=="delete"], reverse=True):
        if 0 <= p < len(doc) and len(doc) > 1: doc.delete_page(p)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success":True,"pageCount":fitz.open(stream=content,filetype="pdf").page_count,"pages":generate_preview(content)}

# ============ ADD IMAGE TO PDF ============
@app.post("/add-image")
async def add_image(pdf_id: str = Form(...), page_num: int = Form(...), image: UploadFile = File(...), x: float = Form(...), y: float = Form(...), width: float = Form(...), height: float = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page = doc[page_num-1]
    
    img_content = await image.read()
    rect = fitz.Rect(x/2.5, y/2.5, (x+width)/2.5, (y+height)/2.5)
    page.insert_image(rect, stream=img_content)
    
    output = io.BytesIO()
    doc.save(output)
    pdf_storage[pdf_id]["content"] = output.getvalue()
    
    mat = fitz.Matrix(2.5, 2.5)
    pix = doc[page_num-1].get_pixmap(matrix=mat)
    doc.close()
    
    return {"success":True,"pageImage":"data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode()}

# ============ ADD SHAPE TO PDF ============
@app.post("/add-shape")
async def add_shape(pdf_id: str = Form(...), page_num: int = Form(...), shape_type: str = Form(...), x: float = Form(...), y: float = Form(...), width: float = Form(...), height: float = Form(...), color: str = Form("#000000"), fill_color: str = Form(""), stroke_width: float = Form(2)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page = doc[page_num-1]
    
    s = 2.5
    ox, oy, ow, oh = x/s, y/s, width/s, height/s
    stroke = tuple(int(color.lstrip('#')[i:i+2],16)/255 for i in (0,2,4))
    fill = tuple(int(fill_color.lstrip('#')[i:i+2],16)/255 for i in (0,2,4)) if fill_color and fill_color != "transparent" else None
    sw = stroke_width/s
    
    if shape_type == "rectangle": page.draw_rect(fitz.Rect(ox,oy,ox+ow,oy+oh), color=stroke, fill=fill, width=sw)
    elif shape_type == "circle": page.draw_circle(fitz.Point(ox+ow/2,oy+oh/2), min(ow,oh)/2, color=stroke, fill=fill, width=sw)
    elif shape_type == "line": page.draw_line(fitz.Point(ox,oy), fitz.Point(ox+ow,oy+oh), color=stroke, width=sw)
    elif shape_type == "arrow":
        end = fitz.Point(ox+ow,oy+oh)
        page.draw_line(fitz.Point(ox,oy), end, color=stroke, width=sw)
        angle = math.atan2(oh,ow)
        for a in [angle-math.pi/6, angle+math.pi/6]:
            page.draw_line(end, fitz.Point(end.x-10*math.cos(a),end.y-10*math.sin(a)), color=stroke, width=sw)
    
    output = io.BytesIO()
    doc.save(output)
    pdf_storage[pdf_id]["content"] = output.getvalue()
    
    mat = fitz.Matrix(2.5, 2.5)
    pix = doc[page_num-1].get_pixmap(matrix=mat)
    doc.close()
    
    return {"success":True,"pageImage":"data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode()}

# ============ WATERMARK ============
@app.post("/add-watermark")
async def add_watermark(pdf_id: str = Form(...), text: str = Form(""), image: UploadFile = File(None), opacity: float = Form(0.3), position: str = Form("center"), rotation: float = Form(-45)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    img_bytes = await image.read() if image else None
    
    for page in doc:
        rect = page.rect
        if text:
            # Text watermark
            fontsize = min(rect.width, rect.height) / 8
            tw = fitz.get_text_length(text, fontsize=fontsize)
            
            if position == "center": x, y = rect.width/2-tw/2, rect.height/2
            elif position == "top-left": x, y = 50, 50+fontsize
            elif position == "top-right": x, y = rect.width-tw-50, 50+fontsize
            elif position == "bottom-left": x, y = 50, rect.height-50
            elif position == "bottom-right": x, y = rect.width-tw-50, rect.height-50
            else: x, y = rect.width/2-tw/2, rect.height/2
            
            page.insert_text(fitz.Point(x,y), text, fontsize=fontsize, color=(0.5,0.5,0.5), rotate=rotation)
        
        if img_bytes:
            # Image watermark
            img = Image.open(io.BytesIO(img_bytes))
            img.putalpha(int(opacity*255))
            out = io.BytesIO()
            img.save(out, format='PNG')
            
            iw, ih = img.size
            scale = min(rect.width/3/iw, rect.height/3/ih)
            nw, nh = iw*scale, ih*scale
            
            if position == "center": x, y = (rect.width-nw)/2, (rect.height-nh)/2
            else: x, y = 50, 50
            
            page.insert_image(fitz.Rect(x,y,x+nw,y+nh), stream=out.getvalue())
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success":True,"pages":generate_preview(content)}

# ============ PAGE NUMBERS ============
@app.post("/add-page-numbers")
async def add_page_numbers(pdf_id: str = Form(...), position: str = Form("bottom-center"), start_num: int = Form(1), format_str: str = Form("Page {n} of {total}")):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    total = len(doc)
    
    for i, page in enumerate(doc):
        rect = page.rect
        text = format_str.replace("{n}", str(start_num+i)).replace("{total}", str(total))
        tw = fitz.get_text_length(text, fontsize=10)
        
        if "bottom" in position: y = rect.height - 30
        else: y = 40
        
        if "left" in position: x = 50
        elif "right" in position: x = rect.width - tw - 50
        else: x = (rect.width - tw) / 2
        
        page.insert_text(fitz.Point(x,y), text, fontsize=10, color=(0,0,0))
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success":True,"pages":generate_preview(content)}

# ============ PASSWORD PROTECT ============
@app.post("/protect-pdf")
async def protect_pdf(pdf_id: str = Form(...), password: str = Form(...), permissions: str = Form("all")):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    perm = fitz.PDF_PERM_ACCESSIBILITY
    if permissions == "all":
        perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_MODIFY
    elif permissions == "print":
        perm |= fitz.PDF_PERM_PRINT
    
    output = io.BytesIO()
    doc.save(output, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, permissions=perm)
    doc.close()
    
    new_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[new_id] = {"content":content,"filename":"protected.pdf","created":datetime.now()}
    
    return {"pdfId":new_id,"fileName":"protected.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

# ============ UNLOCK PDF ============
@app.post("/unlock-pdf")
async def unlock_pdf(file: UploadFile = File(...), password: str = Form(...)):
    content = await file.read()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        if doc.is_encrypted:
            if not doc.authenticate(password):
                raise HTTPException(400, "Wrong password")
        
        output = io.BytesIO()
        doc.save(output)
        doc.close()
        
        new_id = str(uuid.uuid4())
        unlocked = output.getvalue()
        pdf_storage[new_id] = {"content":unlocked,"filename":"unlocked.pdf","created":datetime.now()}
        
        return {"pdfId":new_id,"pages":generate_preview(unlocked),"fileName":"unlocked.pdf"}
    except Exception as e:
        raise HTTPException(400, str(e))

# ============ PDF TO IMAGES ============
@app.post("/pdf-to-images")
async def pdf_to_images(file: UploadFile = File(...), format: str = Form("png"), dpi: int = Form(150)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    images = []
    mat = fitz.Matrix(dpi/72, dpi/72)
    
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        
        if format == "jpg":
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=90)
            img_bytes = out.getvalue()
            mime = "image/jpeg"
        else:
            img_bytes = pix.tobytes("png")
            mime = "image/png"
        
        img_id = str(uuid.uuid4())
        images.append({"id":img_id,"page":i+1,"data":"data:"+mime+";base64,"+base64.b64encode(img_bytes).decode(),"filename":f"page_{i+1}.{format}"})
    
    doc.close()
    return {"images":images}

# ============ IMAGES TO PDF ============
@app.post("/images-to-pdf")
async def images_to_pdf(files: List[UploadFile] = File(...), page_size: str = Form("A4")):
    sizes = {"A4":(595,842),"Letter":(612,792),"Legal":(612,1008),"A3":(842,1191)}
    w, h = sizes.get(page_size, (595,842))
    
    doc = fitz.open()
    
    for f in files:
        img_bytes = await f.read()
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
    
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    
    pdf_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content":content,"filename":"images.pdf","created":datetime.now()}
    
    return {"pdfId":pdf_id,"pageCount":len(files),"pages":generate_preview(content),"fileName":"images.pdf","fileSize":f"{len(content)/1024:.1f} KB"}

# ============ HTML TO PDF ============
@app.post("/html-to-pdf")
async def html_to_pdf(html: str = Form(...), page_size: str = Form("A4")):
    sizes = {"A4":(595,842),"Letter":(612,792)}
    w, h = sizes.get(page_size, (595,842))
    
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    
    # Simple HTML text extraction and rendering
    text = re.sub('<[^<]+?>', '', html)
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    
    page.insert_text(fitz.Point(50, 50), text, fontsize=12)
    
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    
    pdf_id = str(uuid.uuid4())
    content = output.getvalue()
    pdf_storage[pdf_id] = {"content":content,"filename":"document.pdf","created":datetime.now()}
    
    return {"pdfId":pdf_id,"pages":generate_preview(content)}

# ============ SIGN PDF (E-SIGNATURE) ============
@app.post("/add-signature")
async def add_signature(pdf_id: str = Form(...), page_num: int = Form(...), signature: str = Form(...), x: float = Form(...), y: float = Form(...), width: float = Form(150), height: float = Form(50)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    page = doc[page_num-1]
    
    # signature is base64 image data
    if "base64," in signature:
        signature = signature.split("base64,")[1]
    
    sig_bytes = base64.b64decode(signature)
    rect = fitz.Rect(x/2.5, y/2.5, (x+width)/2.5, (y+height)/2.5)
    page.insert_image(rect, stream=sig_bytes)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    
    mat = fitz.Matrix(2.5, 2.5)
    pix = doc[page_num-1].get_pixmap(matrix=mat)
    doc.close()
    
    return {"success":True,"pageImage":"data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode()}

# ============ OCR (TEXT EXTRACTION) ============
@app.post("/ocr-pdf")
async def ocr_pdf(file: UploadFile = File(...)):
    content = await file.read()
    doc = fitz.open(stream=content, filetype="pdf")
    
    full_text = ""
    pages_text = []
    
    for i, page in enumerate(doc):
        text = page.get_text()
        pages_text.append({"page":i+1,"text":text})
        full_text += f"--- Page {i+1} ---\n{text}\n\n"
    
    doc.close()
    return {"fullText":full_text,"pages":pages_text}

# ============ FLATTEN PDF (FORMS) ============
@app.post("/flatten-pdf")
async def flatten_pdf(pdf_id: str = Form(...)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    for page in doc:
        # Flatten annotations
        for annot in page.annots() or []:
            annot.update()
    
    output = io.BytesIO()
    doc.save(output, deflate=True)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success":True,"pages":generate_preview(content)}

# ============ ROTATE ALL PAGES ============
@app.post("/rotate-all")
async def rotate_all(pdf_id: str = Form(...), angle: int = Form(90)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    doc = fitz.open(stream=pdf_storage[pdf_id]["content"], filetype="pdf")
    
    for page in doc:
        page.set_rotation(page.rotation + angle)
    
    output = io.BytesIO()
    doc.save(output)
    content = output.getvalue()
    pdf_storage[pdf_id]["content"] = content
    doc.close()
    
    return {"success":True,"pages":generate_preview(content)}

# ============ REPAIR PDF ============
@app.post("/repair-pdf")
async def repair_pdf(file: UploadFile = File(...)):
    content = await file.read()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        
        # Clean and repair
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True, clean=True)
        doc.close()
        
        repaired = output.getvalue()
        pdf_id = str(uuid.uuid4())
        pdf_storage[pdf_id] = {"content":repaired,"filename":"repaired.pdf","created":datetime.now()}
        
        return {"pdfId":pdf_id,"pages":generate_preview(repaired),"fileName":"repaired.pdf","fileSize":f"{len(repaired)/1024:.1f} KB"}
    except Exception as e:
        raise HTTPException(400, f"Could not repair: {str(e)}")

# ============ PAYMENT ============
@app.post("/create-checkout-session")
async def create_checkout(pdf_id: str = Form(...), edits: str = Form(...), price: int = Form(199)):
    if pdf_id not in pdf_storage: raise HTTPException(404, "PDF not found")
    
    session_id = str(uuid.uuid4())
    payment_sessions[session_id] = {"pdf_id":pdf_id,"edits":edits,"price":price}
    
    checkout = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{'price_data':{'currency':'usd','product_data':{'name':'PDF Download'},'unit_amount':price},'quantity':1}],
        mode='payment',
        success_url=f"{DOMAIN}/payment-success?session_id={session_id}",
        cancel_url=f"{DOMAIN}/"
    )
    return {"checkoutUrl":checkout.url}

@app.get("/payment-success")
async def payment_success(session_id: str):
    return HTMLResponse(f'<html><head><meta http-equiv="refresh" content="1;url=/download-pdf?session_id={session_id}"></head><body style="background:#0f172a;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><div style="text-align:center"><h1 style="color:#10b981">✓ Success!</h1><p>Downloading...</p></div></body></html>')

@app.get("/download-pdf")
async def download_pdf(session_id: str):
    if session_id not in payment_sessions: raise HTTPException(404, "Session not found")
    s = payment_sessions[session_id]
    if s["pdf_id"] not in pdf_storage: raise HTTPException(404, "PDF not found")
    
    content = pdf_storage[s["pdf_id"]]["content"]
    edits = json.loads(s["edits"])
    
    if edits:
        doc = fitz.open(stream=content, filetype="pdf")
        for e in edits:
            page = doc[e["pageNum"]-1]
            rect = fitz.Rect(e["originalX"]-1, e["originalY"]-1, e["originalX"]+e["originalWidth"]+5, e["originalY"]+e["originalHeight"]+1)
            page.draw_rect(rect, color=(1,1,1), fill=(1,1,1))
            if e.get("newText"):
                page.insert_text(fitz.Point(e["originalX"], e["originalY"]+e["originalHeight"]-2), e["newText"], fontsize=e["originalFontSize"])
        out = io.BytesIO()
        doc.save(out)
        doc.close()
        content = out.getvalue()
    
    del payment_sessions[session_id]
    return StreamingResponse(io.BytesIO(content), media_type="application/pdf", headers={"Content-Disposition":"attachment; filename=luleit-edited.pdf"})

@app.get("/download-direct/{pdf_id}")
async def download_direct(pdf_id: str):
    if pdf_id not in pdf_storage: raise HTTPException(404)
    s = pdf_storage[pdf_id]
    return StreamingResponse(io.BytesIO(s["content"]), media_type="application/pdf", headers={"Content-Disposition":f"attachment; filename={s['filename']}"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
