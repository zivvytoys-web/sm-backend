import os
import sys

# Ensure backend root is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import uvicorn
from src.config.env import settings
from src.app import app


def start_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = True):
    """Entry point to launch the FastAPI server via Uvicorn."""
    print(f"Starting {settings.PROJECT_NAME} on http://{host}:{port} (Reload: {reload})")
    uvicorn.run(
        "src.app:app",
        host=host,
        port=port,
        reload=reload
    )


if __name__ == "__main__":
    start_server()
