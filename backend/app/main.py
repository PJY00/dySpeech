from fastapi import FastAPI

from app.api.router import register_api

app = FastAPI(title="dySpeech API")
register_api(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
