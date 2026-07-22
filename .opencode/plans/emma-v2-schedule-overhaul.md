# Emma v2 — Schedule & Telegram Overhaul

## Phase 1: Personal Profile System
- `core/profile/manager.py` — ProfileManager with SQLite key-value store
- `api/routes/profile.py` — CRUD endpoints
- Categories: personal, work, study, projects, contacts, habits, preferences
- Register in `main.py` + `api/deps.py`

## Phase 2: Enhanced Project & Study Tracking
- `core/tasks/project.py` — Project model with progress, milestones
- `core/tasks/study.py` — StudyLog model
- Enhanced `Task` with estimated_hours, completed_hours, milestone_id
- API routes for projects + study logs

## Phase 3: 3-Day AI Schedule Generation
- Enhanced `TimetableManager.build_multi_day(days=3)`
- Injects profile data, project status, pending tasks into AI prompt
- `POST /schedule/build-3day` endpoint
- Telegram `/schedule` command

## Phase 4: Emergency Task / Schedule Reshuffle
- `core/schedule/emergency.py` — reshape logic
- `POST /schedule/emergency` endpoint
- Telegram `/urgent` command

## Phase 5: Appointment Booking via Telegram
- New `appointment` table + handlers in telegram.py
- `/book`, `/cancel`, `/myslots` commands
- Confirmation flow

## Phase 6: Contact Communication Planner
- Tag blocks with contact labels
- AI allocates optimal contact times in schedule
- `/talkto <name>` books a slot

## Phase 7: Enhanced Daily Routines
- Profile-based routine templates
- Morning/focused/break/meeting segments
- Different by day type
