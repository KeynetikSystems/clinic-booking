"""Seed the clinic's 5 doctors on first start (only if the doctors table is empty)."""
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Doctor

DOCTORS = [
    ("Dr. Shadrack Otieno", time(8, 0), time(16, 0)),
    ("Dr. Brian Ngotho", time(9, 30), time(17,30)),
    ("Dr. Carol Rose", time(12, 0), time(21, 0)),
    ("Dr. Fatuma Hassan", time(8, 30), time(14, 30)),
    ("Dr. Peter Lumumba", time(10, 0), time(18, 0)),
]


def seed_doctors(db: Session) -> None:
    if db.scalars(select(Doctor.id).limit(1)).first() is not None:
        return
    db.add_all(Doctor(name=n, start_time=a, end_time=b) for n, a, b in DOCTORS)
    db.commit()
