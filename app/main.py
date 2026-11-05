"""FastAPI production entry point."""
import os
import uvicorn
from src.app import app

__all__ = ["app"]

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting server on {host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port)
