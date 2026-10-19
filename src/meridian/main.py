from fastapi import FastAPI

from meridian import __version__
from meridian.config import get_settings

app = FastAPI(title="Meridian", version=__version__)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "env": get_settings().env}
