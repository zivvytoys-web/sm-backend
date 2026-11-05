# Social AI Platform - Backend

Production-ready, enterprise-scale backend for the **Social AI Platform** built with **FastAPI**, **SQLAlchemy 2.0**, **MySQL**, **Redis**, and **Celery**.

---

## 🏗 Architecture Overview

```
backend/
├── app/
│   ├── main.py                   # FastAPI entry point & lifespan events
│   ├── core/                     # Configuration, JWT Security, RBAC & Logging
│   ├── db/                       # SQLAlchemy Engine, Base & Session Dependencies
│   ├── models/                   # SQLAlchemy ORM Models (MySQL)
│   ├── schemas/                  # Pydantic v2 Request & Response schemas
│   ├── api/                      # Modular API Routers & Endpoints
│   ├── services/                 # Core Business Logic Layer
│   ├── integrations/             # AI (OpenAI/Stability), Social (IG/FB/LI/YT), Storage & Stripe
│   ├── workers/                  # Celery Tasks (AI Gen, Scheduler Queue, Analytics Sync)
│   ├── scheduler/                # Peak-time Slot Algorithms & Daily Queue Managers
│   └── utils/                    # Enums, Timezone, Idempotency & Error Handlers
├── alembic/                      # Database Schema Migrations
├── tests/                        # Pytest Automated Test Suite
├── scripts/                      # Admin user creation & rich data seeding
├── Dockerfile                    # Containerization definition
└── docker-compose.yml            # Multi-service stack (API, MySQL, Redis, Celery)
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+
- MySQL 8.0+
- Redis 6.0+

### 2. Setup Virtual Environment

```bash
cd backend
python -m venv .venv

# On Windows (PowerShell):
.venv\Scripts\Activate.ps1

# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

```bash
cp .env.example .env
```
Edit `.env` with your database credentials, JWT secret, and API keys.

### 5. Seed Initial Data (Optional)

```bash
# Create initial super admin user
python scripts/create_admin.py

# Seed full development demo dataset (Brands, Posts, Analytics, Campaigns)
python scripts/seed_development_data.py
```

### 6. Run the Application

```bash
# Start FastAPI Development Server
uvicorn app.main:app --reload --port 8000
```

Open interactive Swagger UI docs: [http://localhost:8000/docs](http://localhost:8000/docs)  
Open ReDoc specifications: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## ⚡️ Celery Background Workers

Start the Celery worker for async AI generation and scheduled publishing:

```bash
# Start Worker
celery -A app.workers.celery_app worker --loglevel=info

# Start Beat (Periodic scheduler)
celery -A app.workers.celery_app beat --loglevel=info
```

---

## 🐳 Docker Stack Deployment

Launch everything with Docker Compose in a single command:

```bash
docker-compose up --build -d
```

Services started:
- `FastAPI API`: http://localhost:8000
- `MySQL Database`: localhost:3306
- `Redis Cache/Broker`: localhost:6379
- `Celery Worker & Beat`: Background Daemons

---

## 🤖 Autonomous AI Content Pipeline & Scheduler

The platform includes a zero-effort **Autonomous Social AI Pipeline** powered by **Google Gemini AI** and **APScheduler**:

### How It Works
1. **Daily Scheduled Generation (`generate_{business_id}`)**:
   - Runs automatically at the business's configured `generation_time` in its timezone (default `Asia/Kolkata`).
   - Analyzes brand profile, audience, tone, and past 30 days of posts to avoid duplication.
   - Generates a strategic creative brief and generates tailored copy for Instagram, Facebook, LinkedIn, YouTube, Twitter, Pinterest.
   - Synthesizes multi-aspect-ratio branded images (`1:1`, `4:5`, `1.91:1`) and async video reels (`9:16`).
   - Executes automated brand accuracy, safety, policy, and platform compliance quality audits.
2. **Review & Approval Gate**:
   - If `auto_approve` is enabled and quality passes, moves directly to `scheduled`.
   - Otherwise, awaits 1-click owner approval, edit, draft save, or component regeneration.
3. **Automatic Publishing Worker (`publish_due_posts`)**:
   - Runs every 60 seconds.
   - Finds scheduled variants whose target posting time has arrived and publishes them to connected social networks.

### Immediate Testing via API
Trigger an instant autonomous run for testing:
```bash
POST /api/v1/pipeline/run-now
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>

{
  "force": true
}
```

---

## 🧪 Running Automated Tests

Run the full pytest suite:

```bash
pytest tests/test_autonomous_pipeline.py tests/test_gemini_integration.py -v
```
