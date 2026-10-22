# Clinic Booking API

REST API for a small clinic (5 doctors, 30-minute slots) built with **FastAPI + PostgreSQL + SQLAlchemy**, tested with **pytest**, deployed on **Render** via **GitHub Actions**.

- **Live URL:** [https://clinic-booking-6aqh.onrender.com] (interactive docs at `/docs`)
- **Deploy branch:** `main`

---

## Section 1: System Design

### Components

Requests flow top to bottom through four layers:

```
HTTP request
   │
   ▼
app/api/appointments.py            Routing, request/response shapes
   │         └── app/schemas/appointment.py    Pydantic validation
   ▼
app/services/booking_service.py    All business rules (no FastAPI imports)
   │
   ▼
app/db/models.py, database.py      SQLAlchemy / PostgreSQL
```

> **Dependency rule:** dependencies point downward only (`api` → `services` → `db`). The service layer never imports from `api` or FastAPI, so it can be tested and reused without the web framework.


| Layer | Path | Responsibility |
|-------|------|----------------|
| API | `app/api/appointments.py` | Routing, request/response shapes |
| Schemas | `app/schemas/appointment.py` | Pydantic validation |
| Services | `app/services/booking_service.py` | All business rules (no FastAPI imports) |
| Database | `app/db/models.py`, `app/db/database.py` | SQLAlchemy models and PostgreSQL connection |

Keeping rules in the service layer means they are unit-testable without HTTP, and a future change of
transport (admin UI, queue worker, gRPC) reuses them unchanged.

### Models

| Model | Fields/Attributes |
|---|---|
| Doctor | `id, name, start_time, end_time` (daily working hours) |
| Patient | `id, name, email` |
| Appointment | `id, doctor_id, patient_id, appointment_time, status (booked/cancelled), cancel_reason` |

Slots are **computed, not stored**: a doctor's free slots for a day are the 30-minute steps between
`start_time` and `end_time`, minus that day's `booked` appointments. Nothing to pre-generate or keep in sync.

### Key decisions

1. **Double booking is prevented by the database, not just by code.** A partial unique index on
   `(doctor_id, appointment_time) WHERE status = 'booked'`. The service checks first to return a friendly
   409, but a check-then-insert alone races under concurrent requests; the index guarantees only one wins and the
   loser gets a 409 too.
2. **Cancel keeps the row** (`status=cancelled` + required reason) instead of deleting it. History is kept, and the partial
   index ignores cancelled rows, so the slot is bookable again immediately.
3. **Reschedule updates the same row.** Releasing the old slot and claiming the new one happen in one
   transaction; if the new slot is invalid or taken, the original booking is untouched.
4. **Business rules** (same for booking and rescheduling): inside the doctor's working hours, aligned to a
   30-minute boundary, not in the past, and (bonus) at least 1 hour from now.
5. **Time zone:** all times are clinic-local and timezone-naive (offsets are rejected with 422). "Now" is
   computed in `CLINIC_TZ` (default `Africa/Nairobi`) rather than the server clock, because cloud hosts run in UTC and
   would otherwise be 3 hours off.
6. **Status codes:** `404` unknown doctor/patient/appointment, `409` slot taken or invalid state (already cancelled),
   `422` invalid input (past, outside hours, off-grid, within 1 hour, missing cancel reason).
7. **Seed data:** the 5 doctors are created on first start if the table is empty. Patients are created via `POST /patients`.

### Trade-offs considered

- **Stored slots vs computed slots:** stored slots make "who has slot X" a simple join, but require generating and
  maintaining rows per doctor per day (and migrating them when hours change). Computed slots are simpler at this scale.
- **Naive local time vs UTC storage:** right for one clinic; a multi-clinic/multi-timezone product should store UTC.
- **`create_all` vs Alembic migrations:** `create_all` on startup is fine for a take-home; add Alembic once the schema starts changing.
- **No auth:** out of scope. Patient identity is a plain `patient_id` today.
- **Web UI:** a dependency-free single page (`app/static/index.html`) served by the API itself, so one deployment covers both. Patients "sign in" by registering name + email once (stored in the browser); there is no real authentication.
- Unhandled-> one patient holding overlapping slots with different doctors; per-doctor days off/breaks; variable
  appointment lengths.

### Growing

Per-weekday schedules and days off (a `DoctorSchedule`/`TimeOff` table), auth + roles, Alembic, email/SMS reminders via a
background worker, appointment types with durations, multi-clinic support (and UTC storage), pagination on list endpoints.

---

## Section 2: API

| Method | Path | Notes |
|---|---|---|
| POST | `/appointments` | `{doctor_id, patient_id, appointment_time}` -> 201 |
| GET | `/doctors/{id}/availability?date=YYYY-MM-DD` | free 30-min slots |
| PATCH | `/appointments/{id}/cancel` | `{cancel_reason}` (required); 409 if already cancelled |
| PATCH | `/appointments/{id}/reschedule` | `{new_time}`; 409 if cancelled |
| GET | `/patients/{id}/appointments` | upcoming, booked only, sorted by date |
| GET | `/doctors` | list doctors |
| POST | `/doctors`, `/patients` | create records |
| GET | `/` | web UI (book, reschedule, cancel); `/docs` = Swagger, `/health` = status |

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

docker run --name clinic-db -e POSTGRES_USER=clinic -e POSTGRES_PASSWORD=clinic123 \
  -e POSTGRES_DB=clinicdb -p 5432:5432 -d postgres

cp .env.example .env
uvicorn app.main:app --reload       # UI: http://localhost:8000  |  API docs: /docs
```

Without `DATABASE_URL` the app falls back to a local SQLite file.

### Tests

```bash
pytest -v
```

Run against in-memory SQLite with the clock controlled in tests. They cover double booking, cancel re-opening the slot,
reschedule releasing the old slot, rescheduling onto a taken slot, cancelling twice, rescheduling a cancelled
appointment, required cancel reason, working-hours/grid/past checks, the 1-hour rule, and the upcoming-appointments list.

---

## Section 3: Deployment & CI/CD

- **Provider:** Render (Web Service + managed PostgreSQL).
- **Branch that deploys:** `main`.
- **Pipeline** (`.github/workflows/ci.yml`):
  1. **On every pull request** and every push to `main`: install dependencies and run `pytest`.
  2. **On push/merge to `main`, only if tests pass:** call the Render deploy hook, then smoke-test the live URL (retrying while the new version starts).
- Render's own auto-deploy is turned **off** so a deploy can only happen through this pipeline, after tests pass.

Setup: create the Render Web Service (build `pip install -r requirements.txt`, start
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`), add a Render PostgreSQL and set `DATABASE_URL` to its Internal URL,
set Auto-Deploy to Off, then add GitHub repo secrets `RENDER_DEPLOY_HOOK_URL` (Render -> Settings -> Deploy Hook) and `APP_URL`.

---

## Section 4: AI Reflection

_Write this in your own words, honestly. Prompts from the assessment:_

1. **What did you use AI for across the four sections?**
   - TODO
2. **One example where an AI suggestion improved your work. What did you prompt it with?**
   - TODO



3. **One example where AI output was wrong or incomplete, and how you caught it.**

-The AI used native browser popups rather than a custom one. The native browser popup can be harder to notice or less believable for some users in my view.


   
4. **Two decisions you made without AI, and why you trusted your own judgment.**

   The decision to work on a html frontend.
   The decision to mark occupied booking slots

   - TODO
