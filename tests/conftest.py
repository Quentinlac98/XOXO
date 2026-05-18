# tests/conftest.py
# ─────────────────────────────────────────────────────────────
#  Fixtures partagées — v4.6.1 test suite
# ─────────────────────────────────────────────────────────────
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# ── Env vars AVANT tout import de l'app ──────────────────────
# (les imports de app.main lisent os.environ au niveau module)
_db_fd, _db_path = tempfile.mkstemp(suffix="-test.db")

os.environ["SECRET_KEY"]        = "test-secret-xoxo-v461"
os.environ["ADMIN_PASSWORD"]    = "test-chuck-bass"
os.environ["BLAIR_SECRET_CODE"] = "TEST0000"
os.environ["BLAIR_VIP_TOKEN"]   = "test-vip-token"
os.environ["DB_PATH"]           = _db_path
os.environ["DEBUG"]             = "false"
os.environ.setdefault("QUESTIONS_PATH", "questions/gossipgirl_qcm.jsonl")


@pytest.fixture(scope="session")
def flask_app():
    """
    Instance Flask avec :
    - APScheduler entièrement mocké (pas de vrais background jobs)
    - SQLite sur fichier temporaire (pas de :memory: — thread-safe)
    - TESTING=True
    """
    mock_job = MagicMock()
    mock_job.id = "mocked-job-id"
    mock_job.next_run_time = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch("apscheduler.schedulers.background.BackgroundScheduler.start"), \
         patch("apscheduler.schedulers.background.BackgroundScheduler.add_job",
               return_value=mock_job), \
         patch("apscheduler.schedulers.background.BackgroundScheduler.remove_job"), \
         patch("apscheduler.schedulers.background.BackgroundScheduler.reschedule_job"), \
         patch("apscheduler.schedulers.background.BackgroundScheduler.get_job",
               return_value=None):

        from app.main import create_app
        _app = create_app()
        _app.config["TESTING"] = True

        with _app.app_context():
            from app.models import db, get_or_create_game_session
            db.create_all()
            get_or_create_game_session()
            db.session.commit()

        yield _app

    # Cleanup fichier temp
    os.close(_db_fd)
    try:
        os.unlink(_db_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def reset_state(flask_app):
    """
    Remet la DB et l'état en mémoire dans un état propre avant chaque test.
    autouse=True → s'applique automatiquement à tous les tests.
    """
    import app.main as m

    with flask_app.app_context():
        from app.models import (
            db, Player, Score, Scoop, ActivityLog,
            GameSession, ReservedSlot, get_or_create_game_session,
        )
        # Suppression dans l'ordre des dépendances FK
        Score.query.delete()
        Scoop.query.delete()
        ActivityLog.query.delete()
        Player.query.delete()
        GameSession.query.delete()
        db.session.commit()

        get_or_create_game_session()
        db.session.commit()

    # Reset état en mémoire
    m._tick_task_generation       = 0
    flask_app.config["quiz_tick_active"]   = False
    flask_app.config["current_answers"]    = {}
    flask_app.config["gg_pick_candidates"] = []
    flask_app.config["gg_pick_index"]      = -1

    yield
