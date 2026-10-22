from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse

from app.api.appointments import appointments_router, doctors_router, patients_router
from app.db import models  # noqa: F401  (register tables)
from app.db.database import Base, SessionLocal, engine
from app.db.seed import seed_doctors
from app.services.booking_service import BookingError


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_doctors(db)
    yield


app = FastAPI(title="Clinic Booking API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(BookingError)
async def booking_error_handler(_: Request, exc: BookingError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(appointments_router)
app.include_router(doctors_router)
app.include_router(patients_router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "running"}


@app.get("/", include_in_schema=False)
def web_ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
