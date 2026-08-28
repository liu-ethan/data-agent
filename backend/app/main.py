from fastapi import FastAPI

from backend.app.logging import setup_logging

setup_logging()
app = FastAPI(title="data-agent")
