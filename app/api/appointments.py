from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas import appointment as s
from app.services import booking_service as svc

appointments_router = APIRouter(prefix="/appointments", tags=["appointments"])
doctors_router = APIRouter(prefix="/doctors", tags=["doctors"])
patients_router = APIRouter(prefix="/patients", tags=["patients"])


# ----- appointments (required) -----
@appointments_router.post("", response_model=s.AppointmentOut, status_code=201)
def create_appointment(body: s.AppointmentCreate, db: Session = Depends(get_db)):
    return svc.book(db, body.doctor_id, body.patient_id, body.appointment_time)


@appointments_router.patch("/{appointment_id}/cancel", response_model=s.AppointmentOut)
def cancel_appointment(
    appointment_id: int, body: s.CancelRequest, db: Session = Depends(get_db)
):
    return svc.cancel(db, appointment_id, body.cancel_reason)


@appointments_router.patch("/{appointment_id}/reschedule", response_model=s.AppointmentOut)
def reschedule_appointment(
    appointment_id: int, body: s.RescheduleRequest, db: Session = Depends(get_db)
):
    return svc.reschedule(db, appointment_id, body.new_time)


# ----- doctors -----
@doctors_router.post("", response_model=s.DoctorOut, status_code=201)
def create_doctor(body: s.DoctorCreate, db: Session = Depends(get_db)):
    return svc.create_doctor(db, body.name, body.start_time, body.end_time)


@doctors_router.get("", response_model=list[s.DoctorOut])
def list_doctors(db: Session = Depends(get_db)):
    return svc.list_doctors(db)


@doctors_router.get("/{doctor_id}/availability", response_model=s.AvailabilityOut)
def doctor_availability(
    doctor_id: int,
    day: date = Query(alias="date", description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    free, unavailable = svc.get_day_slots(db, doctor_id, day)
    return s.AvailabilityOut(
        doctor_id=doctor_id,
        date=day,
        slot_minutes=int(svc.SLOT.total_seconds() // 60),
        slots=free,
        unavailable=[s.UnavailableSlot(time=t, reason=r) for t, r in unavailable],
    )


# ----- patients -----
@patients_router.post("", response_model=s.PatientOut, status_code=201)
def create_patient(body: s.PatientCreate, db: Session = Depends(get_db)):
    return svc.create_patient(db, body.name, body.email)


@patients_router.get("/{patient_id}/appointments", response_model=list[s.AppointmentOut])
def patient_appointments(patient_id: int, db: Session = Depends(get_db)):
    return svc.list_patient_appointments(db, patient_id)
