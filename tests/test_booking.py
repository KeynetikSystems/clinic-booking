from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models  # noqa: F401
from app.db.database import Base, get_db
from app.main import app
from app.services import booking_service as svc


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


DAY = date.today() + timedelta(days=3)


def at(h, m=0):
    return datetime.combine(DAY, time(h, m)).isoformat()


@pytest.fixture()
def ids(client):
    doc = client.post(
        "/doctors", json={"name": "Dr. Achieng", "start_time": "09:00", "end_time": "12:00"}
    ).json()
    p1 = client.post("/patients", json={"name": "Amani", "email": "amani@example.com"}).json()
    p2 = client.post("/patients", json={"name": "Wanjiru", "email": "w@example.com"}).json()
    return {"doctor": doc["id"], "p1": p1["id"], "p2": p2["id"]}


def book(client, ids, patient, when):
    return client.post(
        "/appointments",
        json={"doctor_id": ids["doctor"], "patient_id": ids[patient], "appointment_time": when},
    )


def slots(client, ids):
    r = client.get(f"/doctors/{ids['doctor']}/availability", params={"date": DAY.isoformat()})
    assert r.status_code == 200
    return r.json()["slots"]


def test_book_success(client, ids):
    r = book(client, ids, "p1", at(9))
    assert r.status_code == 201
    assert r.json()["status"] == "booked"


def test_double_booking_fails(client, ids):
    assert book(client, ids, "p1", at(9)).status_code == 201
    r = book(client, ids, "p2", at(9))
    assert r.status_code == 409


def test_availability_excludes_booked_slot(client, ids):
    assert at(9, 30) in slots(client, ids)
    book(client, ids, "p1", at(9, 30))
    assert at(9, 30) not in slots(client, ids)
    assert len(slots(client, ids)) == 5  # 09:00-12:00 => 6 slots, one taken


def test_cancel_reopens_slot(client, ids):
    appt = book(client, ids, "p1", at(10)).json()
    r = client.patch(f"/appointments/{appt['id']}/cancel", json={"cancel_reason": "Feeling better"})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert r.json()["cancel_reason"] == "Feeling better"
    assert at(10) in slots(client, ids)
    # and someone else can now take it
    assert book(client, ids, "p2", at(10)).status_code == 201


def test_cancel_twice_fails(client, ids):
    appt = book(client, ids, "p1", at(10)).json()
    body = {"cancel_reason": "Changed plans"}
    assert client.patch(f"/appointments/{appt['id']}/cancel", json=body).status_code == 200
    r = client.patch(f"/appointments/{appt['id']}/cancel", json=body)
    assert r.status_code == 409
    assert "already cancelled" in r.json()["detail"]


def test_reschedule_releases_old_slot(client, ids):
    appt = book(client, ids, "p1", at(9)).json()
    r = client.patch(f"/appointments/{appt['id']}/reschedule", json={"new_time": at(11)})
    assert r.status_code == 200
    assert r.json()["appointment_time"].startswith(at(11)[:16])
    free = slots(client, ids)
    assert at(9) in free
    assert at(11) not in free


def test_reschedule_to_taken_slot_fails_and_keeps_original(client, ids):
    a = book(client, ids, "p1", at(9)).json()
    book(client, ids, "p2", at(10))
    r = client.patch(f"/appointments/{a['id']}/reschedule", json={"new_time": at(10)})
    assert r.status_code == 409
    assert at(9) not in slots(client, ids)  # original still held


def test_cannot_reschedule_cancelled(client, ids):
    a = book(client, ids, "p1", at(9)).json()
    client.patch(f"/appointments/{a['id']}/cancel", json={"cancel_reason": "x"})
    r = client.patch(f"/appointments/{a['id']}/reschedule", json={"new_time": at(10)})
    assert r.status_code == 409


@pytest.mark.parametrize("h,m", [(8, 0), (12, 0), (9, 15)])
def test_invalid_times_rejected(client, ids, h, m):
    assert book(client, ids, "p1", at(h, m)).status_code == 422


def test_past_time_rejected(client, ids):
    past = datetime.combine(date.today() - timedelta(days=1), time(9)).isoformat()
    assert book(client, ids, "p1", past).status_code == 422


def test_unknown_doctor_404(client, ids):
    r = client.post(
        "/appointments",
        json={"doctor_id": 999, "patient_id": ids["p1"], "appointment_time": at(9)},
    )
    assert r.status_code == 404


def test_patient_appointments(client, ids):
    book(client, ids, "p1", at(10))
    book(client, ids, "p1", at(9))
    r = client.get(f"/patients/{ids['p1']}/appointments")
    assert r.status_code == 200
    times = [a["appointment_time"] for a in r.json()]
    assert times == sorted(times) and len(times) == 2


def test_cancel_requires_reason(client, ids):
    a = book(client, ids, "p1", at(9)).json()
    assert client.patch(f"/appointments/{a['id']}/cancel").status_code == 422
    assert client.patch(f"/appointments/{a['id']}/cancel", json={"cancel_reason": ""}).status_code == 422


def test_booking_within_one_hour_rejected(client, ids, monkeypatch):
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(8, 30)))
    r = book(client, ids, "p1", at(9))  # only 30 min away
    assert r.status_code == 422 and "1 hour" in r.json()["detail"]
    assert book(client, ids, "p1", at(9, 30)).status_code == 201  # exactly 1 hour away is allowed


def test_availability_hides_slots_within_one_hour(client, ids, monkeypatch):
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(8, 30)))
    free = slots(client, ids)
    assert at(9) not in free and at(9, 30) in free and len(free) == 5


def test_reschedule_within_one_hour_rejected(client, ids, monkeypatch):
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(7, 0)))
    a = book(client, ids, "p1", at(11)).json()
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(8, 45)))
    r = client.patch(f"/appointments/{a['id']}/reschedule", json={"new_time": at(9)})
    assert r.status_code == 422


def test_patient_appointments_upcoming_only(client, ids, monkeypatch):
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(7, 0)))
    book(client, ids, "p1", at(9))
    book(client, ids, "p1", at(10))
    c = book(client, ids, "p1", at(11)).json()
    client.patch(f"/appointments/{c['id']}/cancel", json={"cancel_reason": "x"})
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(9, 30)))  # 09:00 now past
    r = client.get(f"/patients/{ids['p1']}/appointments")
    assert [a["appointment_time"][11:16] for a in r.json()] == ["10:00"]


def test_list_doctors_and_ui(client, ids):
    r = client.get("/doctors")
    assert r.status_code == 200 and r.json()[0]["name"] == "Dr. Achieng"
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert client.get("/health").json() == {"status": "running"}


def test_availability_marks_unavailable_slots(client, ids, monkeypatch):
    monkeypatch.setattr(svc, "_now", lambda: datetime.combine(DAY, time(8, 30)))
    book(client, ids, "p1", at(10))
    r = client.get(f"/doctors/{ids['doctor']}/availability", params={"date": DAY.isoformat()}).json()
    assert {u["time"][11:16]: u["reason"] for u in r["unavailable"]} == {"09:00": "too_soon", "10:00": "booked"}
    assert "10:00" not in [x[11:16] for x in r["slots"]]
