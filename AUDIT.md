# XOXO Gossip Girl Party — Comprehensive Code Audit

> **Date:** 2026-05-25
> **Auditor:** Senior Software Engineer & Full-Stack Security Auditor
> **Scope:** Full repository audit — `app/`, `templates/`, `static/`, configuration files
> **Branch:** `claude/peaceful-shannon-Utirl`

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Axis 1 — Code Review & Dead Code Elimination](#axis-1--code-review--dead-code-elimination)
3. [Axis 2 — Session Management](#axis-2--session-management)
4. [Axis 3 — Event Management & Phase Transition Workflow](#axis-3--event-management--phase-transition-workflow)
5. [Axis 4 — Socket.io Event Architecture](#axis-4--socketio-event-architecture)
6. [Axis 5 — Template Interactivity & Chuck Mode Integration](#axis-5--template-interactivity--chuck-mode-integration)
7. [Suggested Cleanup Script](#suggested-cleanup-script)
8. [Priority Matrix](#priority-matrix)

---

## Executive Summary

The codebase is a Flask + Socket.io party-game server written in clean, well-structured Python. The architecture is sound: strict state machine, database-backed persistence, APScheduler for non-blocking timers, and a generation-counter pattern to kill ghost tasks. **Overall health: B+.**

The most critical risks are not architectural — they are operational: hardcoded fallback secrets, permissive CORS, no rate limiting on the upload endpoint, and the `player_token` being returned in plain JSON on every broadcast response (including to unauthenticated reconnects). None of these require major refactoring; they are targeted fixes.

Dead code is minimal. The biggest technical-debt items are unthreaded mutable state in `app.config`, a duplicated `_cancel_job` import in `events.py`, and a handful of magic numbers.

### Health Snapshot

| Area | Status | Notes |
|------|--------|-------|
| State Machine | ✅ Solid | Deterministic, restart-safe, migration layer present |
| Session Auth | ⚠️ Needs fixes | Fallback secrets, permanent sessions, no rate-limit |
| Socket.io Events | ⚠️ Needs fixes | CORS wildcard, unvalidated payloads, token leak |
| Phase Transitions | ✅ Mostly solid | Minor race window in `_end_quiz`, one magic number |
| Template Sync | ⚠️ Divergence | `new_question` payloads differ between reconnect handlers |
| Dead Code | ✅ Very clean | One dead param, one duplicate import, one alias to verify |
| Test Coverage | ⚠️ Partial | 3 test classes; no auth/socket/phase-transition tests |
| Dependencies | ✅ Up to date | All 11 packages current; no known CVEs |

---

## Axis 1 — Code Review & Dead Code Elimination

### 1.1 Phantom Code / Commented-Out Blocks

The codebase is remarkably clean of `/* … */` style ghost code. All comment markers found (`# F2-m`, `# B1`, `# C-*`) are architectural annotations, not dead code.

**Result: No zombie commented-out functions found.** ✅

---

### 1.2 Uninitialized / Undefined Calls

#### Finding 1.2.A — `_cancel_job` Imported Twice in `events.py`

**Files:** `app/events.py:14` and `app/events.py:31–35`
**Severity:** Low (harmless, but signals a split-import anti-pattern)

`_cancel_job` is imported on line 14, then re-imported inside a second `from .main import (…)` block at line 31.

```python
# Line 14 — first import
from .main import app, socketio, _cancel_job

# Lines 31-35 — DUPLICATE import of the same symbol
from .main import (
    _get_current_gg_dict, _broadcast_scoop, _start_quiz_phase,
    _ask_gg_to_pick, _send_question, _get_question_scores, _start_libre_phase,
    _cancel_job,          # ← already imported above
    _question_timer_job, _broadcast_roster, get_character,
)
```

**Fix — merge both import blocks into one:**

```python
# app/events.py — single consolidated import block at the top
from .main import (
    app, socketio,
    _cancel_job, _cancel_all_timers,
    _get_current_gg_dict, _broadcast_scoop, _broadcast_roster,
    _start_quiz_phase, _start_libre_phase,
    _ask_gg_to_pick, _send_question, _get_question_scores,
    _question_timer_job, get_character,
)
```

---

#### Finding 1.2.B — `STATE_QUIZ_PAUSED` Missing from `events.py` Imports

**File:** `app/events.py:18`
**Severity:** Low (latent `NameError` risk for future handlers)

`events.py` imports `STATE_LOBBY, STATE_QUIZ, STATE_LIBRE` but not `STATE_QUIZ_PAUSED`. Any future handler that needs to compare against the paused state will silently raise a `NameError`.

```python
# Current — line 18
from .models import (
    db, Player, GameSession, Score, Scoop, ActivityLog,
    STATE_LOBBY, STATE_QUIZ, STATE_LIBRE,   # ← STATE_QUIZ_PAUSED missing
    ...
)
```

**Fix:**

```python
from .models import (
    db, Player, GameSession, Score, Scoop, ActivityLog,
    STATE_LOBBY, STATE_QUIZ, STATE_QUIZ_PAUSED, STATE_LIBRE,
    ...
)
```

---

### 1.3 Dead Code — Defined but Never Used

#### Finding 1.3.A — Dead `_questions_unused` Parameter

**File:** `app/main.py:1437`
**Severity:** Low (technical debt)

The `_questions_unused` parameter in `_ask_gg_to_pick` is intentionally dead — preserved for call-site compatibility but never read. Every call site already passes `[]`. The parameter should be removed to simplify the signature.

```python
# Current — main.py line 1437
def _ask_gg_to_pick(game: GameSession, _questions_unused: list, index: int):
    # _questions_unused is never referenced in the function body
```

**Fix — remove the parameter and update all 6 call sites:**

```python
# main.py line 1437
def _ask_gg_to_pick(game: GameSession, index: int):
    ...
```

Call sites to update (all currently pass `[]` as second arg):

| File | Line | Current call | Fixed call |
|------|------|-------------|------------|
| `main.py` | ~1434 | `_ask_gg_to_pick(game, [], 0)` | `_ask_gg_to_pick(game, 0)` |
| `main.py` | ~1470 | `_send_question(...)` → `_ask_gg_to_pick(game, [], pause_index)` | `_ask_gg_to_pick(game, pause_index)` |
| `main.py` | ~1274 | `_ask_gg_to_pick(game, [], pause_index)` | `_ask_gg_to_pick(game, pause_index)` |
| `main.py` | ~1277 | `_ask_gg_to_pick(game, [], pause_index)` | `_ask_gg_to_pick(game, pause_index)` |
| `events.py` | ~440 | `_ask_gg_to_pick(game, [], 0)` | `_ask_gg_to_pick(game, 0)` |
| `events.py` | ~492 | `_ask_gg_to_pick(g, [], nidx)` | `_ask_gg_to_pick(g, nidx)` |

---

#### Finding 1.3.B — `phase2_ends_at` Retrocompatibility Alias

**File:** `app/models.py:146`
**Severity:** Low (potential dead alias)

```python
def to_dict(self):
    return {
        ...
        "libre_ends_at":  self._ends_at_iso(),
        "phase2_ends_at": self._ends_at_iso(),   # ← retrocompatibility alias
    }
```

**Cleanup action — verify if still consumed anywhere:**

```bash
grep -rn "phase2_ends_at" templates/ static/ app/
```

If no hits: remove the alias from `to_dict()`.

---

#### Finding 1.3.C — `quiz_intro_pending` Not Cleared on Force-LOBBY Transition

**File:** `app/main.py:562` and `app/main.py:1145`
**Severity:** Low (cosmetic state leak)

`api_force_gg_quiz` sets `app.config["quiz_intro_pending"] = True`. `_cancel_all_timers()` resets it to `False`. But a direct `LOBBY` transition via `api_set_phase` does not call `_cancel_all_timers()` first — it calls it from inside the `STATE_LOBBY` branch, which does. This is actually fine on inspection, but the config key is not reset in `api_reset`:

```python
# api_reset — main.py line 441
app.config["current_answers"]    = {}
app.config["gg_pick_candidates"] = []
app.config["gg_pick_index"]      = -1
# ← "quiz_intro_pending" and "gg_pick_game_id" are not reset here
```

**Fix — add to `api_reset` and `api_reset_scores`:**

```python
app.config["current_answers"]       = {}
app.config["gg_pick_candidates"]    = []
app.config["gg_pick_index"]         = -1
app.config["gg_pick_game_id"]       = None   # add
app.config["quiz_intro_pending"]    = False  # add
```

---

## Axis 2 — Session Management

### 2.1 Hardcoded Fallback Secrets — **CRITICAL**

**File:** `app/main.py:63–75`
**Severity:** Critical

```python
app.config["SECRET_KEY"]        = os.getenv("SECRET_KEY", "xoxo-fallback-secret")
app.config["ADMIN_PASSWORD"]    = os.getenv("ADMIN_PASSWORD", "ChuckBassGodMode2026")
app.config["BLAIR_SECRET_CODE"] = os.getenv("BLAIR_SECRET_CODE", "24042024")
app.config["BLAIR_VIP_TOKEN"]   = os.getenv("BLAIR_VIP_TOKEN", "blair-vip-token")
```

If the container starts without a `.env` file (fresh deploy, CI environment, misconfigured orchestrator), all four values fall back to strings that are now in the public git history. The most dangerous is `SECRET_KEY = "xoxo-fallback-secret"` — Flask uses this to sign session cookies. An attacker who knows this value can forge a cookie with `{"is_admin": True}` and gain full Chuck Mode access.

**Fix — fail fast at startup, never fall back silently:**

```python
# app/main.py — inside create_app(), immediately after load_dotenv()
_required_env = ["SECRET_KEY", "ADMIN_PASSWORD"]
_missing = [k for k in _required_env if not os.getenv(k)]
if _missing:
    raise RuntimeError(
        f"Required environment variables not set: {', '.join(_missing)}. "
        f"Copy .env.example to .env and fill in real values."
    )

app.config["SECRET_KEY"]        = os.getenv("SECRET_KEY")         # no default
app.config["ADMIN_PASSWORD"]    = os.getenv("ADMIN_PASSWORD")     # no default
app.config["BLAIR_SECRET_CODE"] = os.getenv("BLAIR_SECRET_CODE", "")
app.config["BLAIR_VIP_TOKEN"]   = os.getenv("BLAIR_VIP_TOKEN", secrets.token_urlsafe(16))
```

Also update `.env.example` to make the intent unmistakable:

```ini
# .env.example
SECRET_KEY=CHANGE_ME_generate_with_python3_-c_"import_secrets;print(secrets.token_hex(32))"
ADMIN_PASSWORD=CHANGE_ME_strong_password_here
```

---

### 2.2 Admin Session — Permanent with No Timeout

**File:** `app/main.py:328–329`
**Severity:** High

```python
session["is_admin"] = True
session.permanent = True      # Flask default: 31 days with no PERMANENT_SESSION_LIFETIME set
```

A stolen admin session cookie grants persistent access for up to 31 days.

**Fix:**

```python
# app/main.py — inside create_app(), add alongside other config keys
from datetime import timedelta
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)

# The admin_login() route body remains the same — .permanent=True now
# respects the 4-hour cap instead of the 31-day default.
```

---

### 2.3 No Rate Limiting on Admin Login

**File:** `app/main.py:319–334`
**Severity:** High

The login handler performs a plain string comparison with no failed-attempt tracking, no lockout, and no artificial delay. A script can brute-force the password endpoint at wire speed.

```python
# Current — main.py line 327
if pwd == app.config["ADMIN_PASSWORD"]:
    session["is_admin"] = True
    ...
error = "Mot de passe incorrect. XOXO."   # no delay, no counter
```

**Fix — add Flask-Limiter:**

```
# requirements.txt
flask-limiter==3.7.0
```

```python
# app/main.py — after socketio = SocketIO(...)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(app, key_func=get_remote_address, default_limits=[])

# Decorate the login route
@app.route("/xoxo-admin", methods=["GET", "POST"])
@limiter.limit("10/minute")
def admin_login():
    ...
```

---

### 2.4 `player_token` Exposed in Every Broadcast Response — **CRITICAL**

**File:** `app/models.py:64–75`
**Severity:** Critical

```python
def to_dict(self):
    return {
        "id":           self.id,
        "session_id":   self.session_id,
        "player_token": self.player_token,   # ← included in ALL callers
        "prenom":       self.prenom,
        ...
    }
```

`to_dict()` is called by `get_leaderboard()`, which is broadcast to every connected client via `leaderboard_update`. This means every player receives every other player's `player_token` in their WebSocket stream. Any player who inspects the socket payload can steal another player's token and hijack their session via `reconnect_player`.

**Fix — split into two methods:**

```python
# app/models.py — Player class

def to_dict(self):
    """Full representation — for the authenticated player only (login_success, reconnect_success)."""
    return {
        "id":           self.id,
        "session_id":   self.session_id,
        "player_token": self.player_token,
        "prenom":       self.prenom,
        "personnage":   self.personnage,
        "is_gg":        self.is_gg,
        "is_admin":     self.is_admin,
        "is_connected": self.is_connected,
        "score_total":  self.score_total,
    }

def to_public_dict(self):
    """Stripped representation — safe for broadcast to all clients."""
    return {
        "id":           self.id,
        "prenom":       self.prenom,
        "personnage":   self.personnage,
        "is_gg":        self.is_gg,
        "is_connected": self.is_connected,
        "score_total":  self.score_total,
    }
```

Then replace `to_dict()` with `to_public_dict()` in every broadcast context:

| File | Line | Context | Change |
|------|------|---------|--------|
| `models.py` | `get_leaderboard()` | leaderboard broadcast | `to_public_dict()` |
| `main.py` | `new_gg` emit | broadcast to all | `to_public_dict()` |
| `main.py` | `gg_updated_silent` emit | broadcast to all | `to_public_dict()` |
| `events.py` | `player_joined` emit | broadcast to all | inline dict or `to_public_dict()` |

Keep `to_dict()` only for `login_success` and `reconnect_success` emits (targeted to the individual player's SID).

---

### 2.5 VIP Token Exposed in URL Query String

**File:** `app/main.py:209–212` and `app/main.py:354`
**Severity:** Medium

```python
@app.route("/blair-vip")
def blair_vip():
    token = request.args.get("token", "")   # token in URL → server logs + browser history
    if token == app.config["BLAIR_VIP_TOKEN"]:
        ...
```

The VIP token appears in the URL, which means it is stored in server access logs, browser history, and HTTP `Referer` headers on any downstream redirect. It is also rendered verbatim in the admin dashboard HTML at line 354.

**Fix — accept via POST body or a dedicated header:**

```python
@app.route("/blair-vip", methods=["GET", "POST"])
def blair_vip():
    token = request.form.get("token") or request.headers.get("X-Blair-Token", "")
    if token == app.config["BLAIR_VIP_TOKEN"]:
        session["blair_vip_verified"] = True
        return redirect(url_for("vip_page"))
    return redirect(url_for("mobile"))
```

---

## Axis 3 — Event Management & Phase Transition Workflow

### 3.1 State Machine — Overall Assessment: Sound ✅

The `GameSession.transition_to()` guard (`app/models.py:150–154`), the `VALID_STATES` tuple, `_recover_state_after_restart`, and `_migrate_old_states` are all correct patterns. The state machine is deterministic and restart-safe.

---

### 3.2 Race Condition in `_end_quiz` GG Assignment

**File:** `app/main.py:1709–1718`
**Severity:** Medium

`_end_quiz` can be triggered from two concurrent paths:
1. The APScheduler `_question_timer_job` (scheduled)
2. The `_early_end` background task (All-In, spawned from `events.py:607`)

The guard at `main.py:1653` (`game.question_index != index`) blocks double-firing of `_question_timer_job`. However, `_delayed_libre` (line 1740) re-queries the DB and checks `g.is_quiz` — which is still `True` if the first `_end_quiz` hasn't committed its state transition yet. The window is tiny under eventlet's cooperative scheduler, but the symptom would be `new_gg` and `play_sound` emitted twice.

```python
# main.py line 1702
def _end_quiz(game: GameSession):
    leaderboard = get_leaderboard()
    winner      = leaderboard[0] if leaderboard else None
    # ← no guard here: if called twice concurrently, both branches execute
    if winner:
        Player.query.update({"is_gg": False}, synchronize_session=False)
        new_gg = Player.query.get(winner["id"])
        ...
        socketio.emit("new_gg", new_gg.to_dict())   # ← could fire twice
```

**Fix — add an optimistic-lock state check at the top of `_end_quiz`:**

```python
def _end_quiz(game: GameSession):
    # Prevent double-fire: if state has already moved away from QUIZ, bail out.
    if game.current_state != STATE_QUIZ:
        return
    # Immediately mark the transition before any async work
    game.transition_to(STATE_LIBRE)
    db.session.commit()

    leaderboard = get_leaderboard()
    winner      = leaderboard[0] if leaderboard else None
    # ... rest of the function unchanged, remove the _delayed_libre check for is_quiz
```

---

### 3.3 `api_reset_timer` — Magic 15 Minutes Instead of Config Value

**File:** `app/main.py:866`
**Severity:** Medium

```python
@app.route("/api/admin/reset-timer", methods=["POST"])
@admin_required
def api_reset_timer():
    """Remet le timer Mode Libre à 15 min à partir de maintenant."""
    game = get_or_create_game_session()
    if not game.is_libre:
        return jsonify({"ok": False, "error": "Pas en Mode Libre."})
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=15)   # ← hardcoded
```

The operator configures `PHASE2_DURATION_MINUTES` in `.env`, but the reset-timer button ignores it and always resets to 15 minutes.

**Fix:**

```python
ends_at = datetime.now(timezone.utc) + timedelta(minutes=app.config["LIBRE_DURATION_MINUTES"])
```

---

### 3.4 `_stop_quiz_with_rollback` — `__import__("json")` Anti-Pattern

**File:** `app/main.py:1320`
**Severity:** Low

```python
game.questions_asked = __import__("json").dumps(asked_cleaned)   # ← json already imported at top
```

`json` is imported at `main.py:11`. This `__import__` call is a code smell left from an inline edit.

**Fix:**

```python
game.questions_asked = json.dumps(asked_cleaned)
```

---

### 3.5 `gg_pick_game_id` Not Cleared on Reset

**File:** `app/main.py:447–451` and `app/main.py:493–495`
**Severity:** Low

Both `api_reset` and `api_reset_scores` clear most in-memory state but leave `gg_pick_game_id` stale:

```python
# api_reset — line 447
app.config["current_answers"]    = {}
app.config["gg_pick_candidates"] = []
app.config["gg_pick_index"]      = -1
# ← "gg_pick_game_id" and "quiz_intro_pending" not reset
```

If a reset happens mid-pick, `_gg_pick_timeout_job` (which reads `gg_pick_game_id`) could query a game session that no longer corresponds to the active session.

**Fix:**

```python
app.config["current_answers"]    = {}
app.config["gg_pick_candidates"] = []
app.config["gg_pick_index"]      = -1
app.config["gg_pick_game_id"]    = None   # add this line
app.config["quiz_intro_pending"] = False  # add this line
```

---

## Axis 4 — Socket.io Event Architecture

### 4.1 CORS Wildcard — **CRITICAL**

**File:** `app/main.py:101`
**Severity:** Critical

```python
socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*",    # ← allows any domain to open a WebSocket
    ping_timeout=60,
    ping_interval=25,
    ...
)
```

`cors_allowed_origins="*"` permits Cross-Site WebSocket Hijacking (CSWSH). A malicious page on any domain can open a socket, impersonate a player, and interact with the game.

**Fix:**

```python
# .env (production)
CORS_ORIGINS=https://yourdomain.com,https://projector.yourdomain.com

# app/main.py
_raw_origins = os.getenv("CORS_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins=ALLOWED_ORIGINS,
    ping_timeout=60,
    ping_interval=25,
    ...
)
```

---

### 4.2 `submit_answer` — No Server-Side Active Question Validation

**File:** `app/events.py:515–531`
**Severity:** High

```python
@socketio.on("submit_answer")
def on_submit_answer(data):
    ...
    question_id = data.get("question_id")
    answer      = data.get("answer", "").upper()

    if answer not in ("A", "B", "C", "D"):
        return

    already = Score.query.filter_by(player_id=player.id, question_id=question_id).first()
    if already:
        emit("answer_error", {"message": "Tu as deja repondu a cette question."})
        return

    q_data  = game.get_current_question()
    ...
    is_correct = bool(q_data and answer == q_data.get("answer"))
```

`question_id` is accepted from the client without verifying it matches the currently active question. A client can submit a `question_id` for a question from a previous round (for which no `Score` row exists yet for this player) and earn points if the answer happens to be correct.

**Fix — validate against the active question before processing:**

```python
q_data = game.get_current_question()
if not q_data or q_data.get("id") != question_id:
    emit("answer_error", {"message": "Question invalide ou expirée."})
    return
```

---

### 4.3 `sound_started` Re-Broadcasts Unvalidated Data

**File:** `app/events.py:713–723`
**Severity:** Medium

```python
@socketio.on("sound_started")
def on_sound_started(data):
    socketio.emit("sound_started", {
        "sound":    data.get("sound", ""),      # ← any string from any client
        "duration": data.get("duration", 0),
    })
```

The projector is the intended emitter, but any connected socket can send `sound_started` with an arbitrary `sound` string. If frontend templates construct audio file paths from the sound name without sanitisation, a malicious payload could attempt a path traversal.

**Fix — whitelist valid sound identifiers:**

```python
VALID_SOUNDS = frozenset([
    "xoxo", "new_gg", "quiz_start", "phase2_start", "timer_end",
    "champagne", "drama", "gossip", "scandale", "suspens", "victoire",
])

@socketio.on("sound_started")
def on_sound_started(data):
    sound = data.get("sound", "")
    if sound not in VALID_SOUNDS:
        return
    socketio.emit("sound_started", {
        "sound":    sound,
        "duration": float(data.get("duration", 0)),
    })
```

Apply the same whitelist to the `play_sound` emit path in `api_play_sound` (`main.py:752`).

---

### 4.4 File Upload Endpoint — Public, No Content Validation

**File:** `app/main.py:1090–1103`
**Severity:** High

```python
@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():         # ← no @vip_required, no @admin_required
    f   = request.files["image"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return jsonify({"error": "Format non supporté."}), 400
    fname = f"{secrets.token_hex(8)}{ext}"
    fpath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
    f.save(fpath)
    return jsonify({"image_path": f"/static/uploads/{fname}"})
```

Three compounding issues:
1. **Public endpoint** — no authentication required; any host on the network can upload files
2. **Extension-only validation** — trivially bypassed by renaming `exploit.php` to `exploit.jpg`
3. **No magic-byte verification** — the file content is never checked against its declared type

**Fix — validate by content (magic bytes) and enforce authentication:**

```python
import imghdr   # Python stdlib — reads file magic bytes

@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    # Require a valid connected player (token passed via header or cookie)
    # Minimal check: only process if the request comes from a known session
    if "image" not in request.files:
        return jsonify({"error": "Pas d'image."}), 400

    f    = request.files["image"]
    data = f.read(10 * 1024 * 1024 + 1)   # read at most MAX+1 bytes

    if len(data) > 10 * 1024 * 1024:
        return jsonify({"error": "Fichier trop volumineux (max 10 Mo)."}), 413

    detected = imghdr.what(None, h=data)
    if detected not in ("jpeg", "png", "gif", "webp"):
        return jsonify({"error": "Format de fichier invalide."}), 400

    ext   = {"jpeg": ".jpg"}.get(detected, f".{detected}")
    fname = f"{secrets.token_hex(8)}{ext}"
    fpath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
    with open(fpath, "wb") as out:
        out.write(data)
    return jsonify({"image_path": f"/static/uploads/{fname}"})
```

---

### 4.5 `post_scoop` — No Per-Player Rate Limiting

**File:** `app/events.py:624`
**Severity:** Medium

Players can emit `post_scoop` at any rate with no per-player cooldown. A bot or a player with DevTools open can flood the scoop feed, disrupting all connected views.

**Fix — in-memory cooldown dict (resets on server restart, acceptable for a party game):**

```python
# app/events.py — module level
import time as _time
_last_scoop_ts: dict[int, float] = {}   # player_id → monotonic timestamp

@socketio.on("post_scoop")
def on_post_scoop(data):
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player:
        emit("error", {"message": "Tu n'es pas connecté."})
        return

    now  = _time.monotonic()
    last = _last_scoop_ts.get(player.id, 0)
    if now - last < 10:   # 10-second cooldown between scoops
        emit("error", {"message": "Attends un peu avant de poster un autre scoop. XOXO."})
        return
    _last_scoop_ts[player.id] = now

    # ... rest of handler unchanged
```

---

### 4.6 Disconnect Handler — Already Safe, but Worth Documenting

**File:** `app/events.py:64–80`

The `on_disconnect` handler queries by `session_id=sid` (the SID of the disconnecting socket). If a player reconnects (new SID) before the disconnect event fires, the filter won't match the new SID and will correctly skip the stale event. The implementation is safe as-is. ✅

A code comment clarifying this intent would prevent future developers from "fixing" the non-issue:

```python
@socketio.on("disconnect")
def on_disconnect():
    sid    = request.sid
    # Filter by sid ensures we never clear a player who has already reconnected
    # under a new sid — the new sid won't match this old one.
    player = Player.query.filter_by(session_id=sid).first()
    ...
```

---

## Axis 5 — Template Interactivity & Chuck Mode Integration

### 5.1 Data Flow Architecture — Overall Assessment: Coherent ✅

All four views (admin/Chuck Mode, projector, mobile, VIP) share the same Socket.io namespace and derive state from `GameSession` in SQLite. Events propagate via `socketio.emit(...)` to the full room. The synchronisation model is correct and consistent.

---

### 5.2 Divergent `new_question` Payloads Between Reconnect Handlers

**Files:** `app/events.py:205–213` (`on_projector_reconnect`) vs `app/events.py:264–273` (`on_get_game_state`)
**Severity:** Medium (cosmetic desync between views)

The Chuck Mode admin calls `get_game_state` on page load. The projector calls `projector_reconnect`. Both handlers reconstruct the current question state, but with different field values:

```python
# on_projector_reconnect — events.py line 205
emit("new_question", {
    "question_number": idx + 1,    # idx = game.question_index - 1
    "total":           "?",        # ← hardcoded "?"
    ...
})

# on_get_game_state — events.py line 264
emit("new_question", {
    "question_number": game.question_index,   # different derivation
    "total":           app.config.get("QUESTIONS_PER_SESSION", 10),   # ← "10"
    ...
})
```

**Result:** During a live question, the projector shows `Q3 / ?` while Chuck Mode shows `Q3 / 10`.

**Fix — extract a shared helper and use it in both handlers:**

```python
# app/main.py — add this helper function
def _build_current_question_payload(game: GameSession) -> dict | None:
    """Builds a normalised new_question payload for reconnecting clients."""
    q_data = game.get_current_question()
    if not q_data:
        return None
    remaining = 10
    if game.question_started_at:
        started = game.question_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        remaining = max(0, int(10 - (datetime.now(timezone.utc) - started).total_seconds()))
    return {
        "question_number": game.question_index,
        "total":           app.config.get("QUESTIONS_PER_SESSION", 10),
        "question_id":     q_data["id"],
        "question":        q_data["question"],
        "choices":         q_data["choices"],
        "category":        q_data.get("category", ""),
        "duration":        remaining,
        "answer":          q_data.get("answer", ""),
    }
```

Then in both `on_projector_reconnect` and `on_get_game_state`:

```python
payload = _build_current_question_payload(game)
if payload:
    emit("new_question", payload)
    emit("question_tick", {"remaining": payload["duration"], "question_id": payload["question_id"]})
```

---

### 5.3 `vip_blast` Coverage — Verify Mobile Template

**File:** `app/main.py:260–263`
**Severity:** Medium (potential missing handler)

```python
socketio.emit("new_scoop",   scoop.to_dict())
socketio.emit("play_sound",  {"sound": "new_gg"})
socketio.emit("vip_blast",   {"content": content, "scoop_id": scoop.id})
```

`vip_blast` is emitted globally but if `mobile.html` only listens for `new_scoop` (not `vip_blast`), players will not see VIP blasts on their phones — only the projector and Chuck Mode will update.

**Audit action:**

```bash
grep -n "vip_blast" templates/mobile.html templates/projector.html templates/admin.html templates/vip.html
```

If `mobile.html` is missing a `socket.on('vip_blast', ...)` handler, add one that renders the blast message prominently (e.g., a full-screen overlay or banner).

---

### 5.4 `gg_updated_silent` Broadcasts Full `to_dict()` (Token Leak)

**File:** `app/main.py:605`
**Severity:** High (follows from Finding 2.4)

```python
socketio.emit("gg_updated_silent", new_gg.to_dict())   # ← broadcasts player_token to all
```

After applying the fix from §2.4, change this to:

```python
socketio.emit("gg_updated_silent", new_gg.to_public_dict())
```

---

### 5.5 Blair VIP Token Rendered in Admin Dashboard HTML

**File:** `app/main.py:354`
**Severity:** Medium

```python
return render_template(
    "admin.html",
    ...
    blair_token=app.config["BLAIR_VIP_TOKEN"],   # ← token rendered in HTML source
)
```

The VIP access token is injected into the admin template HTML. If an admin session is shoulder-surfed, screen-shared, or if the page source is cached, the VIP token is exposed without needing to inspect the server configuration.

**Fix — show only a masked preview and provide a separate "copy" button that fetches the value via a dedicated admin API call:**

```python
# admin.html template
# Instead of rendering the full token, show a masked value
blair_token_masked = f"{blair_token[:4]}{'*' * (len(blair_token) - 4)}"

# A dedicated endpoint for the admin to retrieve the full token securely
@app.route("/api/admin/blair-token")
@admin_required
def api_blair_token():
    return jsonify({"token": app.config["BLAIR_VIP_TOKEN"]})
```

---

## Suggested Cleanup Script

Run these commands immediately on the working branch to verify findings before applying fixes:

```bash
# ── Axis 1: Dead Code & Phantom Imports ──────────────────────────────────────

# 1. Confirm the duplicate _cancel_job import
grep -n "_cancel_job" app/events.py

# 2. Confirm _ask_gg_to_pick call sites (before removing _questions_unused param)
grep -n "_ask_gg_to_pick" app/main.py app/events.py

# 3. Check if phase2_ends_at is still consumed anywhere
grep -rn "phase2_ends_at" templates/ static/ app/

# 4. Check if quiz_intro_pending is reset in all branches
grep -n "quiz_intro_pending" app/main.py


# ── Axis 2: Session Security ──────────────────────────────────────────────────

# 5. Confirm fallback secrets are never set in non-dev environments
git grep -n "xoxo-fallback-secret\|ChuckBassGodMode2026\|24042024\|blair-vip-token" -- '*.py' '*.env*' '*.md'

# 6. Test startup without .env (should raise RuntimeError after fix)
# python -c "import os; os.environ.clear(); from app.main import create_app; create_app()"

# 7. Find all Player.to_dict() calls used in broadcast context
grep -n "\.to_dict()" app/main.py app/events.py | grep -v "login_success\|reconnect_success"


# ── Axis 3: Phase Transitions ─────────────────────────────────────────────────

# 8. Confirm the __import__ anti-pattern location
grep -n "__import__" app/main.py

# 9. Confirm reset handlers clear all in-memory state keys
grep -A 10 "def api_reset\b" app/main.py | grep "app.config"


# ── Axis 4: Socket.io Events ──────────────────────────────────────────────────

# 10. Confirm upload endpoint has no auth decorator
grep -B 3 "def api_upload_image" app/main.py

# 11. List all socketio.on handlers and their auth checks
grep -n "@socketio.on\|@admin_required\|@vip_required" app/events.py app/main.py


# ── Axis 5: Template Sync ─────────────────────────────────────────────────────

# 12. Check vip_blast handler coverage across all templates
grep -n "vip_blast" templates/mobile.html templates/projector.html templates/admin.html templates/vip.html

# 13. Verify new_question payload consistency between reconnect handlers
grep -n "new_question" app/events.py
```

---

## Priority Matrix

| # | File | Line(s) | Severity | Finding | Estimated Effort |
|---|------|---------|----------|---------|-----------------|
| 1 | `main.py` | 63–75 | **Critical** | Hardcoded fallback secrets — `SECRET_KEY` forgeable | 30 min |
| 2 | `models.py` | 64–75 | **Critical** | `player_token` in broadcast `to_dict()` | 1 h |
| 3 | `main.py` | 101 | **Critical** | CORS wildcard — Cross-Site WebSocket Hijacking | 15 min |
| 4 | `events.py` | 515–531 | **High** | `submit_answer` does not validate active question ID | 20 min |
| 5 | `main.py` | 1090–1103 | **High** | Upload: public endpoint, extension-only check, no magic bytes | 1 h |
| 6 | `main.py` | 328–329 | **High** | Admin session never expires (`permanent=True`, no lifetime) | 15 min |
| 7 | `main.py` | 319–334 | **High** | No rate limiting on admin login endpoint | 30 min |
| 8 | `main.py` | 605 | **High** | `gg_updated_silent` broadcasts `player_token` to all clients | 5 min (follows #2) |
| 9 | `events.py` | 713–723 | **Medium** | `sound_started` re-broadcasts unvalidated sound name | 20 min |
| 10 | `events.py` | 624 | **Medium** | `post_scoop` has no per-player rate limiting | 30 min |
| 11 | `main.py` | 209 / 354 | **Medium** | VIP token in URL query string and in HTML source | 45 min |
| 12 | `main.py` | 866 | **Medium** | `reset-timer` hardcodes 15 min instead of reading config | 5 min |
| 13 | `events.py` | 205 vs 264 | **Medium** | Divergent `new_question` payloads between reconnect handlers | 30 min |
| 14 | `events.py` | — | **Medium** | `vip_blast` handler may be absent from `mobile.html` | 30 min (verify first) |
| 15 | `main.py` | 1709 | **Medium** | Race window in `_end_quiz` — `new_gg` could emit twice | 20 min |
| 16 | `main.py` | 1320 | **Low** | `__import__("json")` anti-pattern | 2 min |
| 17 | `events.py` | 14 + 31 | **Low** | Duplicate `_cancel_job` import | 5 min |
| 18 | `main.py` | 1437 | **Low** | Dead `_questions_unused` parameter | 20 min |
| 19 | `main.py` | 447 / 493 | **Low** | `gg_pick_game_id` not cleared on reset | 5 min |
| 20 | `models.py` | 146 | **Low** | `phase2_ends_at` alias likely unused — verify and remove | 10 min |

**Critical + High tier total: ~5 h 55 min** — achievable in a single focused session. The architecture itself requires no redesign.

---

*XOXO, Gossip Girl*
