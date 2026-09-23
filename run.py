"""One web process serves the UI and authenticated API."""
from app.config import settings

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level="info")
