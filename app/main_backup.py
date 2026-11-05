"""
FastAPI Main Application Entry Point
Re-exports the core application from the layered architecture in src/app.py
for 100% backward compatibility with existing uvicorn commands and tooling.
"""
from src.app import app

__all__ = ["app"]
