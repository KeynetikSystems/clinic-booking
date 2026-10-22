from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def _example_time(hour: int) -> str:
    """A valid-looking future slot for the Swagger examples (a week out, on the hour)."""
    d = datetime.now() + timedelta(days=7)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _naive(v: datetime) -> datetime:
    if v.tzinfo is not None:
        raise ValueError("Use clinic-local time without a timezone offset, e.g. 2026-11-02T09:30:00")
    return v


class DoctorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def _check_hours(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class DoctorOut(DoctorCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class PatientOut(PatientCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"doctor_id": 1, "patient_id": 1, "appointment_time": _example_time(9)}]})
    doctor_id: int
    patient_id: int
    appointment_time: datetime

    _tz = field_validator("appointment_time")(_naive)


class CancelRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"cancel_reason": "Patient is unavailable"}]})
    cancel_reason: str = Field(min_length=1, max_length=500, description="Why the appointment is being cancelled")


class RescheduleRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"new_time": _example_time(10)}]})
    new_time: datetime

    _tz = field_validator("new_time")(_naive)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    patient_id: int
    appointment_time: datetime
    status: str
    cancel_reason: Optional[str] = None


class UnavailableSlot(BaseModel):
    time: datetime
    reason: Literal["booked", "too_soon"]


class AvailabilityOut(BaseModel):
    doctor_id: int
    date: date
    slot_minutes: int
    slots: list[datetime]  # free slots only
    unavailable: list[UnavailableSlot] = []  # booked / too soon, so UIs can grey them out
