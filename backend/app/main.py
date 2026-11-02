from fastapi import FastAPI

app = FastAPI(title="dySpeech API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
