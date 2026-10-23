# Clinic Booking API

A production-style clinic appointment scheduling system built with FastAPI, PostgreSQL, and SQLAlchemy.

The application enables patients to book, reschedule, and cancel appointments while preventing double bookings through database-level constraints and transactional business logic. It includes automated tests, CI/CD deployment via GitHub Actions, interactive API documentation, and a lightweight web interface for non-technical users

| | |
|---|---|
| **Live app** | https://clinic-booking-6aqh.onrender.com |
| **API docs (Swagger)** | https://clinic-booking-6aqh.onrender.com/docs |
| **Deploy branch** | `main` |

> The app runs on a free Render plan. After a period of inactivity it sleeps, so the first request can take up to a minute while it wakes.

---

## Section 1: System Design

### Components

Requests flow downward through four layers:

```
HTTP request
   │
   ▼
app/api/appointments.py          Routing, request/response shapes
   │      └── app/schemas/appointment.py   Pydantic validation
   ▼
app/services/booking_service.py  All business rules (no FastAPI imports)
   │
   ▼
app/db/models.py, database.py    SQLAlchemy models and the PostgreSQL connection
```

**Dependency rule:** dependencies point downward only (`api` → `services` → `db`). The service layer never imports FastAPI, so the rules can be tested without HTTP, and a future entry point (admin UI, queue worker, gRPC) can reuse them unchanged.

### Models

| Model | Fields |
|---|---|
| **Doctor** | `id, name, start_time, end_time` (daily working hours) |
| **Patient** | `id, name, email` |
| **Appointment** | `id, doctor_id, patient_id, appointment_time, status (booked / cancelled), cancel_reason` |

Slots are **computed, not stored**. A doctor's free slots for a day are the 30-minute steps between `start_time` and `end_time`, minus that day's `booked` appointments. There is nothing to pre-generate or keep in sync.

### Key decisions

1. **The database prevents double booking, not just the code.** A partial unique index on `(doctor_id, appointment_time) WHERE status = 'booked'` allows one active booking per doctor per slot. The service checks first so it can return a friendly `409`, but a check-then-insert alone can race under concurrent requests. With the index, only one request wins and the other also gets a `409`.
2. **Cancel keeps the row** (`status = cancelled` plus a required reason) instead of deleting it. History is preserved, and because the index ignores cancelled rows, the slot can be booked again immediately.
3. **Reschedule updates the same row.** The old slot is released and the new one claimed in a single transaction. If the new slot is invalid or taken, the original booking is untouched.
4. **One set of rules for booking and rescheduling:** the time must be inside the doctor's working hours, on a 30-minute boundary, not in the past, and (bonus) at least 1 hour from now.
5. **The 1-hour rule is interpreted as "at least 1 hour of notice".** The brief says to prevent bookings *within* 1 hour of now. I read that as a minimum lead time, so a slot exactly 60 minutes away is allowed and anything closer is rejected. The error message says "at least 1 hour in advance". The stricter reading is a one-character change (`<` to `<=`) in the service.
6. **Time zone:** all times are clinic-local and timezone-naive (offsets are rejected with `422`). "Now" is computed in `CLINIC_TZ` (default `Africa/Nairobi`) rather than from the server clock, because cloud hosts run in UTC and would otherwise be 3 hours off.
7. **Status codes:** `404` unknown doctor, patient or appointment; `409` slot taken or invalid state (for example, already cancelled); `422` invalid input (past, outside hours, off the 30-minute grid, within 1 hour, missing cancel reason).
8. **Seed data:** the 5 doctors are created on first start if the table is empty. Patients are created through `POST /patients`.

### Trade-offs considered

- **Stored vs computed slots:** stored slots make "who has slot X?" a simple join, but need rows generated per doctor per day, and migrated whenever hours change. Computed slots are simpler at this scale.
- **Naive local time vs UTC storage:** naive local time suits a single clinic. A multi-clinic or multi-timezone product should store UTC.
- **`create_all` vs Alembic migrations:** `create_all` on startup is fine for this scope. Alembic should be added once the schema starts changing.
- **No authentication:** out of scope for this exercise. Patient identity is a plain `patient_id`.
- **Web page:** a dependency-free single page (`app/static/index.html`) served by the API itself, so one deployment covers both. Patients "sign in" by registering a name and email once (remembered in the browser). This is convenience, not real authentication.
- **Not handled:** a patient holding overlapping slots with different doctors, per-doctor days off and breaks, and variable appointment lengths.

### Growing the system

- **Per-weekday schedules and days off:** a `DoctorSchedule` and `TimeOff` table, read by the slot calculation.
- **Authentication and roles:** take the patient identity from a token instead of the request body.
- **Reminders:** a background worker sending email or SMS for upcoming appointments.
- **Appointment types with durations:** needs an overlap rule (for example, a Postgres exclusion constraint on time ranges) in place of the single-slot index.
- **Multiple clinics:** a clinic table, UTC storage with a time zone per clinic, and Alembic migrations.
- **Pagination** on list endpoints.

---

## Section 2: API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/appointments` | Body `{doctor_id, patient_id, appointment_time}` → `201`; `409` if the slot is taken |
| `GET` | `/doctors/{id}/availability?date=YYYY-MM-DD` | `slots` = free 30-minute slots; `unavailable` = booked or too-soon slots (lets the page grey them out) |
| `PATCH` | `/appointments/{id}/cancel` | Body `{cancel_reason}` (required); `409` if already cancelled |
| `PATCH` | `/appointments/{id}/reschedule` | Body `{new_time}`; `409` if the appointment is cancelled |
| `GET` | `/patients/{id}/appointments` | Upcoming booked appointments, sorted by date |
| `GET` | `/doctors` | List doctors |
| `POST` | `/doctors`, `/patients` | Create records |
| `GET` | `/` | Web page (book, reschedule, cancel) |
| `GET` | `/docs`, `/health` | Swagger UI, health check |

Times are clinic-local ISO strings without an offset, for example `2026-12-12T09:00:00`.

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# PostgreSQL via Docker
docker run --name clinic-db -e POSTGRES_USER=clinic -e POSTGRES_PASSWORD=clinic123 \
  -e POSTGRES_DB=clinicdb -p 5432:5432 -d postgres

cp .env.example .env                 # Windows: copy .env.example .env
uvicorn app.main:app --reload        # page: http://localhost:8000   docs: /docs
```

- If `DATABASE_URL` is not set, the app falls back to a local SQLite file, so you can run it without Docker.
- If port 8000 is unavailable (common on Windows), add `--port 8085`.

### Tests

```bash
pytest -v
```

The tests run against in-memory SQLite, with the clock controlled by the test. They cover:

- double booking and booking an already-taken slot;
- cancelling, which reopens the slot, and rejecting a second cancel;
- rescheduling, which releases the old slot, and rejecting a move onto a taken slot or of a cancelled appointment;
- the required cancel reason;
- working-hours, 30-minute-grid and past-time checks;
- the 1-hour rule;
- availability, including booked and too-soon slots;
- the upcoming-appointments list.

Production runs on PostgreSQL. The partial index uses syntax supported by both databases.

---

## Section 3: Deployment and CI/CD

- **Provider:** Render (Web Service plus managed PostgreSQL).
- **Public URL:** https://clinic-booking-6aqh.onrender.com
- **Branch that deploys:** `main`.

### Pipeline (`.github/workflows/ci.yml`)

1. **Every pull request, and every push to `main`:** install dependencies and run `pytest`.
2. **Only on a push or merge to `main`, and only if the tests pass:** call the Render deploy hook, then smoke-test the live URL (retrying while the new version starts).

Render's own auto-deploy is switched **off**, so a deploy can only happen through this pipeline, after the tests pass.

### One-time setup

1. Create a Render Web Service from the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
2. Create a Render PostgreSQL database in the same region, and set the service's `DATABASE_URL` environment variable to its **Internal Database URL**.
3. Set Auto-Deploy to **Off**.
4. Add two GitHub repository secrets:
   - `RENDER_DEPLOY_HOOK_URL`: from Render → Settings → Deploy Hook
   - `APP_URL`: `https://clinic-booking-6aqh.onrender.com` (no trailing slash)

---

## Section 4: AI Reflection

### 1. What did you use AI for across the four sections?

- **Section 1 (design):** drafting the data models.
- **Section 2 (API):** [service layer / endpoints, if true], and the tests.
- **Section 3 (deployment):** the GitHub Actions workflow.
- **Front end:** the booking page, which I then reviewed and refined.

### 2. One example where an AI suggestion improved your work. What did you prompt it with?

>  Polishing the double booking prevention algorithm. I prompted it with "There is a risk of two patients booking one doctor in one slots resulting in a doctor being double book.How should we prevent double booking?"

### 3. One example where AI output was wrong or incomplete, and how you caught it.

- **What was incomplete:** The AI-generated UI initially displayed only available appointment slots. During manual testing, I found that this created ambiguity because users could not distinguish between unavailable slots and missing data.
- **How I caught it:** by using the page as a patient would. A time that vanishes looks like an error, whereas a greyed-out slot labelled "Booked" or "Too soon" clearly says the slot exists but cannot be taken.
- **What I did:** I asked for the occupied slots to be shown greyed out with labels, and to mark the patient's own booking as "Yours".
- A smaller fix: the first version also used the browser's built-in `confirm()` pop-up for bookings and reschedules. I judged it easy to overlook and less trustworthy for a medical appointment, so I had it replaced with a styled in-page confirmation dialog matching the cancel dialog.

### 4. Two decisions you made without AI. Why did you trust your own judgment?

- **I decided to build an HTML front end** served by the API itself, instead of leaving Swagger as the only interface.
  - *Why I trusted my own judgment:* Swagger is not intended for everyday patients. I therefore chose to provide a dedicated web interface that abstracts the underlying API and presents appointment information in a more accessible way.
- **I chose to show occupied slots greyed out and labelled**, instead of hiding them from the list.
  - *Why I trusted my own judgment:* The AI's first version listed only free slots. From a patient's point of view, a missing time looks like an error, while a greyed-out "Booked" slot clearly says it exists but is taken. I recognised that gap and prompted the AI to build a visual cue to close the gap.