from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, configs, moderation, users, media, stats


app = FastAPI(title="FB Spy API", version="0.1.0")

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(configs.router)
app.include_router(moderation.router)
app.include_router(users.router)
app.include_router(media.router)
app.include_router(stats.router)


@app.get("/health")
async def health():
    return {"ok": True}
