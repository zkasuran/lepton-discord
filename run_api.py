"""Entry point: start the FastAPI payment server."""

import uvicorn

from src.payments.config import API_HOST, API_PORT

if __name__ == "__main__":
    uvicorn.run(
        "src.api.app:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info",
    )
