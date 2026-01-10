# Luleit PDF Editor - Deployment Guide

## Quick Start (15 minutes to live)

### Step 1: Create Stripe Account (5 min)
1. Go to https://stripe.com and create an account
2. Complete verification (can use test mode while waiting)
3. Go to Developers → API Keys
4. Copy your **Publishable key** (pk_test_xxx or pk_live_xxx)
5. Copy your **Secret key** (sk_test_xxx or sk_live_xxx)

### Step 2: Deploy to Railway (5 min) - RECOMMENDED
Railway is the easiest option with free tier.

1. Go to https://railway.app
2. Sign up with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Connect your GitHub and create a new repo with these files
5. Or click "Deploy from template" → "Empty Project" → "Add Service" → "GitHub Repo"

**Set Environment Variables in Railway:**
```
STRIPE_SECRET_KEY=sk_test_your_key_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
DOMAIN=https://your-app.railway.app
```

6. Railway will auto-deploy. Get your URL from the dashboard.

### Alternative: Deploy to Render (5 min)
1. Go to https://render.com
2. Sign up with GitHub
3. Click "New" → "Web Service"
4. Connect your repo
5. Set environment variables (same as above)
6. Deploy

### Step 3: Get a Domain (5 min)
**Option A: Use Railway/Render subdomain (FREE)**
- Railway: your-app.up.railway.app
- Render: your-app.onrender.com

**Option B: Custom Domain ($10-15/year)**
1. Buy domain from:
   - Porkbun (cheapest): https://porkbun.com
   - Namecheap: https://namecheap.com
   - Cloudflare: https://cloudflare.com/products/registrar

2. In Railway/Render, go to Settings → Custom Domain
3. Add your domain (e.g., luleit.com)
4. Update DNS records as instructed:
   - Usually add a CNAME record pointing to your Railway/Render URL

### Step 4: Setup Stripe Webhook
1. Go to Stripe Dashboard → Developers → Webhooks
2. Click "Add endpoint"
3. Enter URL: https://yourdomain.com/webhook
4. Select events: `checkout.session.completed`
5. Copy the Webhook signing secret (whsec_xxx)
6. Add it to your environment variables as STRIPE_WEBHOOK_SECRET

### Step 5: Go Live with Stripe
1. Complete Stripe account verification
2. Switch from test keys to live keys
3. Update environment variables with live keys
4. Test a real $1 payment!

---

## File Structure
```
pdf-editor-production/
├── main.py              # FastAPI backend with Stripe
├── index.html           # Frontend
├── requirements.txt     # Python dependencies
├── Procfile            # For Heroku/Railway
├── railway.json        # Railway config
├── render.yaml         # Render config
└── README.md           # This file
```

## Environment Variables
| Variable | Description | Example |
|----------|-------------|---------|
| STRIPE_SECRET_KEY | Stripe secret key | sk_live_xxx |
| STRIPE_PUBLISHABLE_KEY | Stripe publishable key | pk_live_xxx |
| STRIPE_WEBHOOK_SECRET | Webhook signing secret | whsec_xxx |
| DOMAIN | Your full domain URL | https://luleit.com |
| PORT | Port (auto-set by host) | 8000 |

## Testing Stripe Payments
Use these test card numbers:
- **Success**: 4242 4242 4242 4242
- **Decline**: 4000 0000 0000 0002
- Any future expiry date, any CVC

## Cost Breakdown
| Service | Cost |
|---------|------|
| Railway Hobby | $5/month (or free tier) |
| Render | Free tier available |
| Domain | $10-15/year |
| Stripe | 2.9% + $0.30 per transaction |

**Revenue Example:**
- 100 downloads/month × $1 = $100
- Stripe fees: ~$33
- Hosting: ~$5
- **Profit: ~$62/month**

## Scaling Tips
1. **Add Redis** for session storage (Railway has Redis addon)
2. **Add S3** for PDF storage (longer retention)
3. **Add analytics** (Plausible, Umami, or Google Analytics)
4. **Add email** (Resend, SendGrid) for receipts

## Support
- Railway docs: https://docs.railway.app
- Render docs: https://render.com/docs
- Stripe docs: https://stripe.com/docs
- FastAPI docs: https://fastapi.tiangolo.com

---

## Quick Deploy Commands

### Push to GitHub
```bash
cd pdf-editor-production
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/yourusername/luleit.git
git push -u origin main
```

### Local Testing
```bash
pip install -r requirements.txt
export STRIPE_SECRET_KEY=sk_test_xxx
export STRIPE_PUBLISHABLE_KEY=pk_test_xxx
export DOMAIN=http://localhost:8000
uvicorn main:app --reload
```

Good luck with your launch! 🚀
