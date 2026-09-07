AetherBeasts RPG — FINAL COMPLETE

Render:
Build: pip install -r requirements.txt
Start: uvicorn bot:app --host 0.0.0.0 --port $PORT

Required Environment:
BOT_TOKEN
DATABASE_URL

Recommended:
RENDER_EXTERNAL_URL=https://YOUR-SERVICE.onrender.com
WEBHOOK_SECRET=optional-secret

This build preserves the full backend and uses the expanded Mini App frontend.
Database migrations add missing Energy/Favorite columns automatically.
