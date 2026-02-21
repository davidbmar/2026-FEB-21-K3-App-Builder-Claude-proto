from fastapi import FastAPI
import os

app = FastAPI(title=os.environ.get("APP_NAME", "app"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": f"Hello from {os.environ.get('APP_NAME', 'app')}"}
