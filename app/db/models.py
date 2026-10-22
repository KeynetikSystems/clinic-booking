from datetime import datetime, time
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

BOOKED = "booked"
CANCELLED = "cancelled"


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    appointment_time: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20), default=BOOKED)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(500), default=None)

    doctor: Mapped[Doctor] = relationship()
    patient: Mapped[Patient] = relationship()

    # Database-level guarantee against double booking: only ONE *booked*
    # appointment per doctor per slot. Cancelled rows don't count, so a
    # cancelled slot can be booked again. This holds even under concurrent
    # requests, where an application-level "check then insert" would race.
    __table_args__ = (
        Index(
            "uq_doctor_active_slot",
            "doctor_id",
            "appointment_time",
            unique=True,
            postgresql_where=text("status = 'booked'"),
            sqlite_where=text("status = 'booked'"),
        ),
    )
