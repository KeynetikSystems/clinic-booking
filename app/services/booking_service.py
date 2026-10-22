"""All booking business rules live here, independent of FastAPI."""
import os
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import BOOKED, CANCELLED, Appointment, Doctor, Patient

SLOT = timedelta(minutes=30)
MIN_LEAD = timedelta(hours=1)  # bookings must start at least 1 hour from now
CLINIC_TZ = ZoneInfo(os.getenv("CLINIC_TZ", "Africa/Nairobi"))


def _now() -> datetime:
    """Current clinic-local time (naive). The server may run in UTC, the clinic doesn't."""
    return datetime.now(CLINIC_TZ).replace(tzinfo=None)


class BookingError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(BookingError):
    status_code = 404


class SlotUnavailableError(BookingError):
    status_code = 409


class InvalidStateError(BookingError):
    status_code = 409


class InvalidRequestError(BookingError):
    status_code = 422


# ---------- helpers ----------
def _get_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if not doctor:
        raise NotFoundError(f"Doctor {doctor_id} not found")
    return doctor


def _get_patient(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if not patient:
        raise NotFoundError(f"Patient {patient_id} not found")
    return patient


def _get_appointment(db: Session, appointment_id: int) -> Appointment:
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise NotFoundError(f"Appointment {appointment_id} not found")
    return appt


def _validate_slot(doctor: Doctor, when: datetime) -> None:
    now = _now()
    if when < now:
        raise InvalidRequestError("Appointment time is in the past")
    if when < now + MIN_LEAD:
        raise InvalidRequestError("Appointments must be booked at least 1 hour in advance")
    day_start = datetime.combine(when.date(), doctor.start_time)
    day_end = datetime.combine(when.date(), doctor.end_time)
    if when < day_start or when + SLOT > day_end:
        raise InvalidRequestError(
            f"Outside doctor's working hours ({doctor.start_time:%H:%M}-{doctor.end_time:%H:%M})"
        )
    if (when - day_start) % SLOT != timedelta(0):
        raise InvalidRequestError("Appointments must start on a 30-minute slot boundary")


def _is_taken(db: Session, doctor_id: int, when: datetime, exclude_id: Optional[int] = None) -> bool:
    stmt = select(Appointment.id).where(
        Appointment.doctor_id == doctor_id,
        Appointment.appointment_time == when,
        Appointment.status == BOOKED,
    )
    if exclude_id is not None:
        stmt = stmt.where(Appointment.id != exclude_id)
    return db.execute(stmt).first() is not None


def _commit(db: Session) -> None:
    """Commit, translating a unique-index violation (race) into a 409."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SlotUnavailableError("That slot has just been taken")


# ---------- use cases ----------
def create_doctor(db: Session, name, start_time, end_time) -> Doctor:
    doctor = Doctor(name=name, start_time=start_time, end_time=end_time)
    db.add(doctor)
    db.commit()
    return doctor


def list_doctors(db: Session) -> list[Doctor]:
    return list(db.scalars(select(Doctor).order_by(Doctor.id)))


def create_patient(db: Session, name, email) -> Patient:
    patient = Patient(name=name, email=email)
    db.add(patient)
    db.commit()
    return patient


def get_day_slots(db: Session, doctor_id: int, day: date):
    """Return (free, unavailable) for a day; unavailable = [(time, 'booked' | 'too_soon')]."""
    doctor = _get_doctor(db, doctor_id)
    day_start = datetime.combine(day, doctor.start_time)
    day_end = datetime.combine(day, doctor.end_time)

    booked = set(
        db.scalars(
            select(Appointment.appointment_time).where(
                Appointment.doctor_id == doctor_id,
                Appointment.status == BOOKED,
                Appointment.appointment_time >= day_start,
                Appointment.appointment_time < day_end,
            )
        )
    )
    earliest = _now() + MIN_LEAD
    free, unavailable, cur = [], [], day_start
    while cur + SLOT <= day_end:
        if cur in booked:
            unavailable.append((cur, "booked"))
        elif cur < earliest:
            unavailable.append((cur, "too_soon"))
        else:
            free.append(cur)
        cur += SLOT
    return free, unavailable


def book(db: Session, doctor_id: int, patient_id: int, when: datetime) -> Appointment:
    doctor = _get_doctor(db, doctor_id)
    _get_patient(db, patient_id)
    _validate_slot(doctor, when)
    if _is_taken(db, doctor_id, when):
        raise SlotUnavailableError("That slot is already booked")
    appt = Appointment(
        doctor_id=doctor_id, patient_id=patient_id, appointment_time=when, status=BOOKED
    )
    db.add(appt)
    _commit(db)
    return appt


def cancel(db: Session, appointment_id: int, reason: Optional[str]) -> Appointment:
    appt = _get_appointment(db, appointment_id)
    if appt.status == CANCELLED:
        raise InvalidStateError("Appointment is already cancelled")
    appt.status = CANCELLED
    appt.cancel_reason = reason
    _commit(db)
    return appt


def reschedule(db: Session, appointment_id: int, new_time: datetime) -> Appointment:
    appt = _get_appointment(db, appointment_id)
    if appt.status != BOOKED:
        raise InvalidStateError("Only booked appointments can be rescheduled")
    doctor = _get_doctor(db, appt.doctor_id)
    _validate_slot(doctor, new_time)
    if _is_taken(db, appt.doctor_id, new_time, exclude_id=appt.id):
        raise SlotUnavailableError("The new slot is already booked")
    # Moving the same row frees the old slot atomically with claiming the new one.
    appt.appointment_time = new_time
    _commit(db)
    return appt


def list_patient_appointments(db: Session, patient_id: int) -> list[Appointment]:
    _get_patient(db, patient_id)
    return list(
        db.scalars(
            select(Appointment)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.status == BOOKED,
                Appointment.appointment_time >= _now(),
            )
            .order_by(Appointment.appointment_time)
        )
    )
