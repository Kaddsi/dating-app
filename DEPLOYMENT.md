# Premium Dating TWA - Complete Deployment Roadmap

## 🚀 From Code to Live Production

### Phase 1: Local Development Setup (Day 1-2)

#### 1.1 Database Setup
```bash
# Install PostgreSQL with PostGIS
sudo apt-get install postgresql-14 postgresql-14-postgis-3

# Create database
sudo -u postgres psql
CREATE DATABASE dating_db;
CREATE USER dating_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE dating_db TO dating_user;

# Enable PostGIS
\c dating_db
CREATE EXTENSION postgis;

# Run schema
psql -U dating_user -d dating_db -f database/schema.sql
```

#### 1.2 Backend Setup
```bash
# Create virtual environment
cd bot
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install aiogram==3.3.0 \
            asyncpg==0.29.0 \
            phonenumbers==8.13.27 \
            fastapi==0.109.0 \
            uvicorn==0.27.0 \
            python-multipart==0.0.6

# Configure environment
cp .env.example .env
# Edit .env with your tokens
```

#### 1.3 Frontend Setup
```bash
cd frontend

# Install dependencies
npm install react@18 \
            react-dom@18 \
            framer-motion@11 \
            tailwindcss@3 \
            @vitejs/plugin-react

# Configure Vite for Telegram WebApp
# See vite.config.js below
```

---

### Phase 2: Production Infrastructure (Day 3-5)

#### 2.1 Database Hosting (AWS RDS or DigitalOcean Managed PostgreSQL)

**Option A: DigitalOcean Managed Database** (Recommended for simplicity)
- Cost: ~$15/month (Basic plan)
- Setup:
  ```bash
  # Create via DigitalOcean dashboard
  # Select PostgreSQL 14+ with PostGIS
  # Choose region closest to your users
  # Enable automatic backups
  ```
- Connection string: `postgresql://user:pass@db-host:25060/dating_db?sslmode=require`

**Option B: AWS RDS**
- Cost: ~$25/month (db.t3.micro)
- Better for scaling
- More configuration required

#### 2.2 Backend Hosting (Docker + VPS or Railway/Render)

**Option A: DigitalOcean Droplet with Docker** (Best performance)
```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY bot/ ./bot/
COPY backend/ ./backend/

# Expose ports
EXPOSE 8000 8080

# Start both bot and API
CMD ["sh", "-c", "python bot/main.py & uvicorn backend.api.swipe_handler:app --host 0.0.0.0 --port 8000"]
```

Deploy:
```bash
# On your droplet
docker build -t dating-twa .
docker run -d \
  -p 8000:8000 \
  -p 8080:8080 \
  -e BOT_TOKEN=your_token \
  -e DATABASE_URL=your_db_url \
  --restart unless-stopped \
  dating-twa
```

**Option B: Railway.app** (Easier, more expensive)
- Connect GitHub repo
- Add PostgreSQL service
- Deploy with zero config
- Cost: ~$20/month

#### 2.3 Frontend Hosting (Vercel)

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
cd frontend
vercel --prod

# Set environment variables in Vercel dashboard:
VITE_API_URL=https://your-backend-url.com
VITE_BOT_USERNAME=@your_bot
```

**Vercel Configuration** (`vercel.json`):
```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "framework": "vite",
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

#### 2.4 Image Storage (Cloudinary or AWS S3)

**Option A: Cloudinary** (Recommended for ease)
```javascript
// Frontend upload
import { Cloudinary } from "@cloudinary/url-gen";

const uploadImage = async (file) => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('upload_preset', 'dating_photos');
  
  const response = await fetch(
    'https://api.cloudinary.com/v1_1/your_cloud_name/image/upload',
    { method: 'POST', body: formData }
  );
  
  const data = await response.json();
  return data.secure_url;
};
```

**Setup:**
- Sign up at cloudinary.com
- Free tier: 25GB storage, 25GB/month bandwidth
- Create upload preset in settings
- Enable auto-optimization for mobile

**Option B: AWS S3 + CloudFront**
- More control, better for large scale
- Setup S3 bucket with public read access
- Add CloudFront CDN for faster delivery
- Use presigned URLs for uploads

---

### Phase 3: Telegram Bot Configuration (Day 6)

#### 3.1 Create Bot with BotFather
```
# In Telegram, message @BotFather
/newbot
# Follow prompts, save your BOT_TOKEN

# Set bot description
/setdescription
Premium dating experience for extraordinary connections ✨

# Set about text  
/setabouttext
Find meaningful connections with Premium Dating 💎

# Set bot commands
/setcommands
start - Start your dating journey

# Enable inline mode (optional)
/setinline

# Set menu button to open Web App
/setmenubutton
# Select your bot
# Choose "Set Menu Button URL"
# Enter: https://your-frontend.vercel.app
```

#### 3.2 Configure Web App
```
# In BotFather
/mybots
# Select your bot
# Bot Settings → Menu Button → Edit Menu Button URL
# Enter your Vercel URL: https://your-dating-app.vercel.app
```

---

### Phase 4: Testing & Launch (Day 7-10)

#### 4.1 Pre-Launch Checklist

**Security:**
- [ ] Environment variables secured (never commit tokens)
- [ ] Database credentials rotated
- [ ] HTTPS enabled on all endpoints
- [ ] Rate limiting implemented
- [ ] CORS configured correctly
- [ ] Telegram auth validation working

**Functionality:**
- [ ] Phone verification blocking works
- [ ] Geo-fencing correctly blocks regions
- [ ] Swipe mechanics smooth on mobile
- [ ] Match notifications sent via bot
- [ ] Images load fast (< 2s)
- [ ] Distance calculations accurate

**Performance:**
- [ ] API response time < 500ms
- [ ] Frontend loads < 3s
- [ ] Database queries optimized with indexes
- [ ] Image CDN configured

**User Experience:**
- [ ] Glassmorphism effects render smoothly
- [ ] Haptic feedback works on supported devices
- [ ] Dark theme consistent throughout
- [ ] Error messages user-friendly

#### 4.2 Monitoring Setup

**Backend Monitoring:**
```python
# Add logging
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger()
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)
```

**Error Tracking:** Use Sentry
```bash
pip install sentry-sdk

# In main.py
import sentry_sdk
sentry_sdk.init(dsn="your_sentry_dsn")
```

**Database Monitoring:**
- Enable slow query log in PostgreSQL
- Set up alerts for connection pool exhaustion
- Monitor disk usage

---

### Phase 5: Scaling (When You Get Users)

#### 5.1 Horizontal Scaling

**Backend:**
```bash
# Add load balancer (Nginx)
# Run multiple API instances
docker-compose up --scale api=3
```

**Database:**
- Add read replicas for discover queries
- Implement Redis cache for hot data
- Move sessions to Redis

#### 5.2 Performance Optimization

**Frontend:**
- Implement lazy loading for images
- Add service worker for offline support
- Preload critical resources

```javascript
// Image lazy loading
const ImageComponent = ({ src }) => {
  const [loaded, setLoaded] = useState(false);
  
  return (
    <img
      src={loaded ? src : '/placeholder.jpg'}
      onLoad={() => setLoaded(true)}
      loading="lazy"
    />
  );
};
```

**Backend:**
- Implement Redis for session/cache
- Use PostgreSQL connection pooling
- Add CDN for static assets

---

### Cost Breakdown (Monthly)

**Starter Plan (~$50/month for 0-1000 users):**
- DigitalOcean Managed PostgreSQL: $15
- DigitalOcean Droplet (2GB RAM): $12
- Vercel Pro (if needed): $20
- Cloudinary Free Tier: $0
- Domain: $1/month
- **Total: ~$48/month**

**Growth Plan (~$150/month for 1k-10k users):**
- Database: $40 (4GB RAM)
- Backend Droplet: $40 (4GB RAM + load balancer)
- Vercel Pro: $20
- Cloudinary: $50
- **Total: ~$150/month**

---

### Quick Start Commands

```bash
# Clone and setup
git clone your-repo
cd premium-dating-twa

# Backend
cd bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Edit .env file
python main.py

# Frontend (new terminal)
cd frontend
npm install
npm run dev

# Database
psql -U dating_user -d dating_db -f ../database/schema.sql
```

---

### Production Deployment Script

```bash
#!/bin/bash
# deploy.sh

# Build frontend
cd frontend
npm run build
vercel --prod

# Build and push backend
cd ../
docker build -t dating-backend .
docker push your-registry/dating-backend:latest

# Deploy to server
ssh your-server << 'EOF'
docker pull your-registry/dating-backend:latest
docker stop dating-app || true
docker rm dating-app || true
docker run -d \
  --name dating-app \
  -p 8000:8000 \
  -e BOT_TOKEN=$BOT_TOKEN \
  -e DATABASE_URL=$DATABASE_URL \
  --restart unless-stopped \
  your-registry/dating-backend:latest
EOF

echo "✅ Deployment complete!"
```

Make executable: `chmod +x deploy.sh`

---

### Environment Variables Reference

**.env (Backend)**
```bash
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
DATABASE_URL=postgresql://user:pass@host:5432/dating_db
API_SECRET_KEY=your-secret-key-here
WEB_APP_URL=https://your-app.vercel.app
CLOUDINARY_CLOUD_NAME=your_cloud
CLOUDINARY_API_KEY=123456789012345
CLOUDINARY_API_SECRET=your_secret
SENTRY_DSN=https://xxx@sentry.io/xxx
ENVIRONMENT=production
```

**.env (Frontend - Vite)**
```bash
VITE_API_URL=https://api.yourdomain.com
VITE_BOT_USERNAME=@your_dating_bot
VITE_CLOUDINARY_CLOUD_NAME=your_cloud
```

---

### Support Contacts & Resources

- **Telegram Bot API:** https://core.telegram.org/bots/api
- **Telegram Web Apps:** https://core.telegram.org/bots/webapps
- **aiogram Docs:** https://docs.aiogram.dev/en/latest/
- **PostgreSQL + PostGIS:** https://postgis.net/
- **Framer Motion:** https://www.framer.com/motion/

Ready to launch! 🚀
