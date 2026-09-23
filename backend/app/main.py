from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="CAT Co-Pilot API")

@app.get("/health")
async def health_check():
    return JSONResponse({"status": "ok"})

@app.get("/ready")
async def readiness_check():
    # To be expanded with DB/Redis checks
    return JSONResponse({"status": "ready"})
