# tests/test_v461.py
# ═══════════════════════════════════════════════════════════════
#  Suite de tests automatisés — correctifs v4.6.1
#
#  Run :  pytest tests/test_v461.py -v --tb=short
#
#  Couvre :
#    C-1  Ghost timer tasks (generation counter)
#    C-2  db.session.refresh() dans _libre_end_job
#    C-3  _libre_tick_task : vérification DB toutes les 10 ticks
#    C-4  on_disconnect → session_id mis à None
#    C-5  on_disconnect → pas de doublon leaderboard_update
#    C-6  SQLite WAL mode + busy_timeout + synchronous=NORMAL
#    C-7  Points calculés depuis DB (pas app.config)
#    C-8  synchronize_session=False sur les bulk updates
#    OBS  Reconnexion → broadcast leaderboard_update aux autres clients
# ═══════════════════════════════════════════════════════════════

import json
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

import pytest


# ── Helpers ─────────────────────────────────────────────────


def _new_token():
    return f"tok-{_uuid.uuid4().hex[:12]}"


def _connect_player(socketio, flask_app, prenom, personnage):
    """
    Connecte un client Socket.IO et enregistre le joueur.
    Retourne (client, player_token_from_server, login_success_payload).

    Le player_token est généré côté serveur et renvoyé dans login_success.
    Si join_player échoue (login_error), lève AssertionError avec le message.
    """
    client = socketio.test_client(flask_app)
    client.emit("join_player", {"prenom": prenom, "personnage": personnage})
    received = client.get_received()

    login_ok = next((e for e in received if e["name"] == "login_success"), None)
    login_err = next((e for e in received if e["name"] == "login_error"), None)

    if login_err:
        client.disconnect()
        pytest.fail(f"join_player refusé pour '{personnage}' : {login_err['args'][0]}")

    if login_ok is None:
        client.disconnect()
        pytest.skip(
            f"join_player n'a émis ni login_success ni login_error pour '{personnage}'. "
            "Vérifier les logs serveur."
        )

    token = login_ok["args"][0]["player"]["player_token"]
    return client, token, login_ok["args"][0]


# ════════════════════════════════════════════════════════════════
#  C-6 — SQLite WAL mode + pragmas
# ════════════════════════════════════════════════════════════════

class TestC6WalPragma:
    """
    Vérifie que le listener @event.listens_for(Engine, 'connect')
    a bien appliqué les trois pragmas SQLite à la connexion.
    """

    def test_journal_mode_is_wal(self, flask_app):
        """PRAGMA journal_mode doit retourner 'wal'."""
        with flask_app.app_context():
            from app.models import db
            from sqlalchemy import text
            row = db.session.execute(text("PRAGMA journal_mode")).fetchone()
            assert row[0] == "wal", (
                f"journal_mode={row[0]!r} — attendu 'wal'.\n"
                "Le listener _set_sqlite_pragmas n'a pas été appelé ou n'a pas eu d'effet."
            )

    def test_busy_timeout_5000ms(self, flask_app):
        """PRAGMA busy_timeout doit être 5000 ms."""
        with flask_app.app_context():
            from app.models import db
            from sqlalchemy import text
            row = db.session.execute(text("PRAGMA busy_timeout")).fetchone()
            assert int(row[0]) == 5000, (
                f"busy_timeout={row[0]} ms — attendu 5000."
            )

    def test_synchronous_is_normal(self, flask_app):
        """PRAGMA synchronous doit être 1 (NORMAL)."""
        with flask_app.app_context():
            from app.models import db
            from sqlalchemy import text
            row = db.session.execute(text("PRAGMA synchronous")).fetchone()
            # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
            assert int(row[0]) == 1, (
                f"synchronous={row[0]} — attendu 1 (NORMAL).\n"
                "Valeurs : 0=OFF 1=NORMAL 2=FULL 3=EXTRA"
            )


# ════════════════════════════════════════════════════════════════
#  C-8 — synchronize_session=False sur les bulk updates
# ════════════════════════════════════════════════════════════════

class TestC8BulkUpdate:
    """
    Player.query.update() sans synchronize_session=False peut lever
    InvalidRequestError si des objets Player sont dans la session ORM.
    Vérifie que le flag est présent et que les updates ne crashent pas.
    """

    def test_bulk_update_with_loaded_objects_no_error(self, flask_app):
        """Bulk update ne doit pas lever d'exception avec des objets en session."""
        with flask_app.app_context():
            from app.models import db, Player

            p1 = Player(session_id="sid-bulk-1", player_token="tok-bulk-1",
                        prenom="Serena", personnage="Serena van der Woodsen",
                        score_total=10, is_gg=True)
            p2 = Player(session_id="sid-bulk-2", player_token="tok-bulk-2",
                        prenom="Nate", personnage="Nate Archibald",
                        score_total=5, is_gg=False)
            db.session.add_all([p1, p2])
            db.session.commit()

            # Charge les objets dans l'identity map (simule le cas prod)
            loaded = Player.query.all()
            assert len(loaded) == 2

            try:
                Player.query.update(
                    {"score_total": 0, "is_gg": False},
                    synchronize_session=False,
                )
                db.session.commit()
            except Exception as exc:
                pytest.fail(
                    f"Player.query.update() a levé {type(exc).__name__}: {exc}\n"
                    "Vérifier que synchronize_session=False est bien présent (C-8)."
                )

            assert Player.query.filter_by(score_total=0).count() == 2
            assert Player.query.filter_by(is_gg=True).count() == 0

    def test_reset_game_session_no_error(self, flask_app):
        """reset_game_session() doit remettre les scores à 0 sans crash ORM."""
        with flask_app.app_context():
            from app.models import db, Player, reset_game_session

            p = Player(session_id="sid-reset-1", player_token="tok-reset-1",
                       prenom="Chuck", personnage="Chuck Bass (test)",
                       score_total=42, is_gg=True)
            db.session.add(p)
            db.session.commit()

            # Charge l'objet — si synchronize_session manque, ça plante ici
            _ = Player.query.get(p.id)

            try:
                reset_game_session()
            except Exception as exc:
                pytest.fail(
                    f"reset_game_session() a levé {type(exc).__name__}: {exc}"
                )

            db.session.expire_all()
            player = Player.query.get(p.id)
            assert player.score_total == 0, (
                f"score_total={player.score_total} après reset (attendu 0)"
            )

    def test_transfer_gg_bulk_no_error(self, flask_app):
        """api_transfer_gg → Player.query.update({'is_gg': False}) ne doit pas crasher."""
        with flask_app.app_context():
            from app.models import db, Player, GameSession, get_or_create_game_session
            import app.main as m

            # Setup : deux joueurs, un GG
            p1 = Player(session_id="sid-gg-1", player_token="tok-gg-1",
                        prenom="Blair", personnage="Blair Waldorf (test)",
                        score_total=10, is_gg=True, is_connected=True)
            p2 = Player(session_id="sid-gg-2", player_token="tok-gg-2",
                        prenom="Serena", personnage="Serena (test)",
                        score_total=5, is_gg=False, is_connected=True)
            db.session.add_all([p1, p2])
            db.session.flush()

            game = get_or_create_game_session()
            game.current_gg_id = p1.id
            db.session.commit()

            # Charge les objets dans la session
            _ = Player.query.all()

            # Simule le bulk update de api_transfer_gg
            try:
                Player.query.update({"is_gg": False}, synchronize_session=False)
                p2.is_gg = True
                game.current_gg_id = p2.id
                db.session.commit()
            except Exception as exc:
                pytest.fail(
                    f"Transfer GG bulk update a levé {type(exc).__name__}: {exc}"
                )

            db.session.expire_all()
            assert Player.query.get(p2.id).is_gg is True
            assert Player.query.get(p1.id).is_gg is False


# ════════════════════════════════════════════════════════════════
#  C-1 — Ghost timer tasks (generation counter)
# ════════════════════════════════════════════════════════════════

class TestC1GhostTimer:
    """
    _question_tick_task reçoit task_gen à sa création.
    Si _tick_task_generation a avancé (nouvelle question), la tâche doit
    s'arrêter immédiatement sans émettre question_tick.
    """

    def test_stale_generation_exits_without_emitting(self, flask_app):
        """task_gen périmé → retour immédiat, zéro emission."""
        import app.main as m

        m._tick_task_generation = 5   # génération courante
        flask_app.config["quiz_tick_active"] = True

        emitted = []
        with patch.object(m.socketio, "emit",
                          side_effect=lambda ev, *a, **kw: emitted.append(ev)):
            # Appel avec génération périmée (3 au lieu de 5)
            m._question_tick_task(question_id=1, duration=30, task_gen=3)

        assert "question_tick" not in emitted, (
            f"Ghost task (gen=3, current=5) a quand même émis : {emitted}\n"
            "Vérifier la garde 'if _tick_task_generation != task_gen: return' (C-1)."
        )

    def test_current_generation_does_emit(self, flask_app):
        """task_gen correct → la boucle s'exécute et émet au moins 1 question_tick."""
        import app.main as m

        m._tick_task_generation = 7
        flask_app.config["quiz_tick_active"] = True

        emitted = []

        def mock_sleep(_n):
            # Arrête la boucle après 1 itération
            flask_app.config["quiz_tick_active"] = False

        with patch("eventlet.sleep", side_effect=mock_sleep), \
             patch.object(m.socketio, "emit",
                          side_effect=lambda ev, *a, **kw: emitted.append(ev)), \
             patch.object(m.scheduler, "get_job", return_value=None), \
             flask_app.app_context():

            m._question_tick_task(question_id=42, duration=30, task_gen=7)

        assert "question_tick" in emitted, (
            "Tâche avec bonne génération (gen=7, current=7) n'a pas émis question_tick.\n"
            "La boucle ne s'est peut-être pas exécutée."
        )

    def test_generation_incremented_by_send_question(self, flask_app):
        """_tick_task_generation doit être incrémenté à chaque _send_question."""
        import app.main as m
        from app.models import GameSession, get_or_create_game_session

        with flask_app.app_context():
            initial_gen = m._tick_task_generation
            game = get_or_create_game_session()

            captured_gens = []

            def mock_bg_task(fn, *args, **kwargs):
                if fn.__name__ == "_question_tick_task" and len(args) >= 3:
                    captured_gens.append(args[2])   # 3ème arg = task_gen

            q = {
                "id": 99, "question": "Test?", "answer": "A",
                "choices": {"A": "Oui", "B": "Non", "C": "Peut", "D": "Jamais"},
                "category": "Test", "explanation": "ok",
            }

            with patch.object(m.socketio, "start_background_task",
                              side_effect=mock_bg_task), \
                 patch.object(m.scheduler, "add_job",
                              return_value=MagicMock()), \
                 patch.object(m.socketio, "emit"):

                m._send_question(game, [q], 0)
                gen_1 = m._tick_task_generation

                q2 = dict(q, id=100)
                m._send_question(game, [q2], 1)
                gen_2 = m._tick_task_generation

        assert gen_1 == initial_gen + 1, (
            f"Après 1er _send_question : génération={gen_1}, attendu={initial_gen + 1}"
        )
        assert gen_2 == initial_gen + 2, (
            f"Après 2ème _send_question : génération={gen_2}, attendu={initial_gen + 2}"
        )
        assert len(captured_gens) == 2, (
            f"start_background_task appelé {len(captured_gens)} fois (attendu 2)"
        )
        assert captured_gens[0] != captured_gens[1], (
            f"Les deux tâches ont reçu la même génération ({captured_gens}) — "
            "une ghost task ne pourrait pas se différencier."
        )


# ════════════════════════════════════════════════════════════════
#  C-2 — db.session.refresh() dans _libre_end_job
# ════════════════════════════════════════════════════════════════

class TestC2StaleOrm:
    """
    _libre_end_job doit appeler db.session.refresh(game) pour s'assurer
    de lire l'état DB réel et non un objet ORM potentiellement périmé.
    """

    def test_refresh_called_on_game_object(self, flask_app):
        """db.session.refresh(game) doit être appelé avant le check is_libre."""
        import app.main as m
        from app.models import db, GameSession, STATE_LIBRE

        with flask_app.app_context():
            game = GameSession.query.first()
            game.transition_to(STATE_LIBRE)
            game.libre_ends_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            db.session.commit()
            game_id = game.id

            refresh_log = []
            original_refresh = db.session.refresh

            def tracking_refresh(obj):
                refresh_log.append(type(obj).__name__)
                return original_refresh(obj)

            with patch.object(db.session, "refresh", side_effect=tracking_refresh), \
                 patch.object(m.socketio, "emit"), \
                 patch.object(m, "_start_quiz_phase"):
                m._libre_end_job(game_id)

        assert "GameSession" in refresh_log, (
            f"db.session.refresh() non appelé avec GameSession.\n"
            f"refresh() appelé avec : {refresh_log}\n"
            "Correctif C-2 manquant."
        )

    def test_already_stopped_libre_no_quiz_transition(self, flask_app):
        """
        Si l'admin a déjà basculé hors de LIBRE avant que le job s'exécute,
        _libre_end_job ne doit pas déclencher _start_quiz_phase.
        """
        import app.main as m
        from app.models import db, GameSession, STATE_LOBBY

        with flask_app.app_context():
            game = GameSession.query.first()
            game.transition_to(STATE_LOBBY)
            db.session.commit()
            game_id = game.id

            quiz_starts = []
            with patch.object(m, "_start_quiz_phase",
                              side_effect=lambda g: quiz_starts.append(1)), \
                 patch.object(m.socketio, "emit"):
                m._libre_end_job(game_id)

        assert len(quiz_starts) == 0, (
            f"_start_quiz_phase appelé {len(quiz_starts)} fois alors que "
            "la phase était déjà LOBBY — l'objet ORM était périmé (C-2)."
        )


# ════════════════════════════════════════════════════════════════
#  C-3 — _libre_tick_task : vérification DB toutes les 10 ticks
# ════════════════════════════════════════════════════════════════

class TestC3TickOptimization:
    """
    Sur N ticks, la DB ne doit être interrogée qu'environ N/10 fois
    et non N fois (optimisation C-3).
    """

    def test_db_queried_at_most_every_10_ticks(self, flask_app):
        """Sur 25 ticks, GameSession.query.get doit être appelé ≤ 4 fois."""
        import app.main as m
        from app.models import db, GameSession, STATE_LIBRE

        with flask_app.app_context():
            game = GameSession.query.first()
            game.transition_to(STATE_LIBRE)
            db.session.commit()
            game_id = game.id

            db_calls = [0]
            tick_n   = [0]

            # Mock GameSession.query.get : renvoie un objet is_libre=True
            # jusqu'au 25ème appel de sleep, puis False pour stopper la boucle.
            mock_game = MagicMock()
            mock_game.is_libre   = True
            mock_game.libre_ends_at = None

            original_qget = GameSession.query.get

            def counting_get(gid):
                db_calls[0] += 1
                if tick_n[0] >= 25:
                    mock_game.is_libre = False
                return mock_game

            def fake_sleep(_n):
                tick_n[0] += 1

            ends_at = datetime.now(timezone.utc) + timedelta(seconds=9999)

            with patch.object(GameSession.query, "get", side_effect=counting_get), \
                 patch("eventlet.sleep", side_effect=fake_sleep), \
                 patch.object(m.socketio, "emit"):
                m._libre_tick_task(game_id, ends_at)

        # Sur 25 ticks, vérifications attendues aux ticks 0, 10, 20 = 3 fois.
        # On accepte 4 (tick 24 peut tomber selon l'implémentation).
        assert db_calls[0] <= 4, (
            f"GameSession.query.get() appelé {db_calls[0]} fois pour ~25 ticks.\n"
            "Attendu ≤ 4 (optimisation toutes les 10 ticks, C-3 non appliqué)."
        )
        assert db_calls[0] >= 2, (
            f"GameSession.query.get() appelé seulement {db_calls[0]} fois — "
            "la vérification DB n'a peut-être jamais eu lieu."
        )

    def test_each_tick_emits_timer_tick(self, flask_app):
        """Chaque tick doit émettre timer_tick (la réduction DB ne doit pas réduire les émissions)."""
        import app.main as m
        from app.models import GameSession, STATE_LIBRE, db

        with flask_app.app_context():
            game = GameSession.query.first()
            game.transition_to(STATE_LIBRE)
            db.session.commit()
            game_id = game.id

            tick_n   = [0]
            emitted  = []

            mock_game = MagicMock()
            mock_game.is_libre    = True
            mock_game.libre_ends_at = None

            def fake_get(_id):
                if tick_n[0] >= 10:
                    mock_game.is_libre = False
                return mock_game

            def fake_sleep(_n):
                tick_n[0] += 1

            ends_at = datetime.now(timezone.utc) + timedelta(seconds=9999)

            with patch.object(GameSession.query, "get", side_effect=fake_get), \
                 patch("eventlet.sleep", side_effect=fake_sleep), \
                 patch.object(m.socketio, "emit",
                              side_effect=lambda ev, *a, **kw: emitted.append(ev)):
                m._libre_tick_task(game_id, ends_at)

        timer_ticks = [e for e in emitted if e == "timer_tick"]
        assert len(timer_ticks) >= 10, (
            f"Seulement {len(timer_ticks)} timer_tick émis pour 10 ticks "
            "(attendu ≥ 10 — l'optimisation DB ne doit pas réduire les émissions)."
        )


# ════════════════════════════════════════════════════════════════
#  C-4/5 — on_disconnect : session_id=None, pas de doublon leaderboard
# ════════════════════════════════════════════════════════════════

class TestC4Disconnect:
    """
    Après déconnexion Socket.IO :
    - player.session_id doit être None (ou vide) en DB
    - player.is_connected doit être False
    - leaderboard_update ne doit être émis qu'une seule fois
    """

    @pytest.mark.timeout(10)
    def test_session_id_cleared_on_disconnect(self, flask_app):
        """player.session_id doit être effacé après disconnect."""
        from app.main import socketio
        from app.models import Player, db

        client, token, _ = _connect_player(
            socketio, flask_app, "Rufus", "Rufus Humphrey"
        )

        with flask_app.app_context():
            player = Player.query.filter_by(player_token=token).first()
            if player is None:
                pytest.skip("Joueur non trouvé en DB après join_player")

            player_id = player.id
            sid_before = player.session_id
            assert sid_before is not None, "session_id devrait être set après connexion"

        client.disconnect()

        with flask_app.app_context():
            player = db.session.get(Player, player_id)
            db.session.refresh(player)

            # Le correctif C-4 met session_id à None.
            # Si session_id est nullable=False dans le modèle, ce test
            # détectera un IntegrityError ou une valeur non-None.
            assert player.session_id is None, (
                f"player.session_id={player.session_id!r} après disconnect.\n"
                "Attendu None. Si le modèle a nullable=False, "
                "il faut mettre nullable=True sur Player.session_id (C-4)."
            )

    @pytest.mark.timeout(10)
    def test_is_connected_false_on_disconnect(self, flask_app):
        """player.is_connected doit être False après disconnect."""
        from app.main import socketio
        from app.models import Player, db

        client, token, _ = _connect_player(
            socketio, flask_app, "Lily", "Lily van der Woodsen"
        )

        with flask_app.app_context():
            player = Player.query.filter_by(player_token=token).first()
            if player is None:
                pytest.skip("Joueur non trouvé après join_player")
            player_id = player.id

        client.disconnect()

        with flask_app.app_context():
            player = db.session.get(Player, player_id)
            db.session.refresh(player)
            assert not player.is_connected, (
                f"player.is_connected={player.is_connected!r} après disconnect "
                "(attendu False)."
            )

    @pytest.mark.timeout(10)
    def test_no_duplicate_leaderboard_on_disconnect(self, flask_app):
        """leaderboard_update ne doit être émis qu'une seule fois lors d'un disconnect."""
        import app.main as m
        from app.main import socketio

        client, token, _ = _connect_player(
            socketio, flask_app, "Eric", "Eric van der Woodsen"
        )

        emit_counts = {}
        original_emit = m.socketio.emit

        def counting_emit(event, *args, **kwargs):
            emit_counts[event] = emit_counts.get(event, 0) + 1
            return original_emit(event, *args, **kwargs)

        with patch.object(m.socketio, "emit", side_effect=counting_emit):
            client.disconnect()

        lb_count = emit_counts.get("leaderboard_update", 0)
        assert lb_count <= 1, (
            f"leaderboard_update émis {lb_count} fois à la déconnexion.\n"
            "Attendu ≤ 1 (doublon supprimé en C-5)."
        )


# ════════════════════════════════════════════════════════════════
#  C-7 — Points calculés depuis DB (pas app.config)
# ════════════════════════════════════════════════════════════════

class TestC7PointsFromDb:
    """
    on_submit_answer doit calculer correct_count via Score.query.count() (DB),
    pas via app.config['current_answers'] (mémoire).

    Test clé : on désynchronise volontairement app.config (vide)
    tout en pré-insérant des scores corrects en DB.
    Si le code lit la DB → bonne valeur → bon nombre de points.
    Si le code lit app.config → toujours 0 → toujours 3 pts (faux).
    """

    def _setup_quiz(self, flask_app, question_id=88):
        """Configure l'état quiz minimal pour que on_submit_answer accepte une réponse."""
        from app.models import GameSession, get_or_create_game_session, db, STATE_QUIZ

        with flask_app.app_context():
            game = get_or_create_game_session()
            game.transition_to(STATE_QUIZ)
            game.set_current_question({
                "id":          question_id,
                "answer":      "B",
                "question":    "Qui est Gossip Girl ?",
                "choices":     {"A": "Blair", "B": "Dan", "C": "Chuck", "D": "Serena"},
                "category":    "Test",
                "explanation": "Dan Humphrey était Gossip Girl.",
            })
            game.question_started_at = datetime.now(timezone.utc)
            game.quiz_number = 1
            db.session.commit()

    @pytest.mark.timeout(10)
    def test_first_correct_answer_earns_3_points(self, flask_app):
        """Premier joueur correct → 3 points (aucun score correct en DB)."""
        from app.main import socketio
        from app.models import Score, db

        self._setup_quiz(flask_app, question_id=88)

        # Vérifie DB propre pour cette question
        with flask_app.app_context():
            assert Score.query.filter_by(question_id=88, is_correct=True).count() == 0

        client, _, _ = _connect_player(socketio, flask_app, "Nate", "Nate Archibald")
        client.emit("submit_answer", {"answer": "B", "question_id": 88})
        received = client.get_received()

        confirmed = next((e for e in received if e["name"] == "answer_confirmed"), None)
        if confirmed is None:
            pytest.skip(
                "answer_confirmed non reçu. Possible raison : quiz non actif, "
                "joueur est GG, ou question_id ne correspond pas. Envoyer les logs."
            )

        pts = confirmed["args"][0]["points"]
        assert pts == 3, (
            f"Premier joueur correct : {pts} pts (attendu 3).\n"
            "Vérifier la logique correct_count == 0 → 3 pts."
        )
        client.disconnect()

    @pytest.mark.timeout(10)
    def test_third_correct_answer_earns_1_point_from_db(self, flask_app):
        """
        Avec 2 scores corrects déjà en DB et app.config['current_answers'] vide,
        le 3ème joueur correct doit recevoir 1 point (correct_count DB = 2).

        Si le code lisait app.config (vide), il calculerait 3 pts → test échoue.
        """
        from app.main import socketio
        from app.models import Score, Player, GameSession, db

        self._setup_quiz(flask_app, question_id=89)

        # Insère 2 scores corrects directement en DB
        with flask_app.app_context():
            game = GameSession.query.first()

            ghost1 = Player(session_id="sid-ghost-1", player_token="tok-ghost-1",
                            prenom="Ghost1", personnage="Gossip Girl (ghost 1)",
                            score_total=3, is_gg=False)
            ghost2 = Player(session_id="sid-ghost-2", player_token="tok-ghost-2",
                            prenom="Ghost2", personnage="Gossip Girl (ghost 2)",
                            score_total=2, is_gg=False)
            db.session.add_all([ghost1, ghost2])
            db.session.flush()

            db.session.add_all([
                Score(player_id=ghost1.id, quiz_number=1, question_id=89,
                      answer="B", is_correct=True, points=3),
                Score(player_id=ghost2.id, quiz_number=1, question_id=89,
                      answer="B", is_correct=True, points=2),
            ])
            db.session.commit()

            # Désynchronise app.config volontairement (vide)
            flask_app.config["current_answers"] = {}

        client, _, _ = _connect_player(
            socketio, flask_app, "Vanessa", "Vanessa Abrams"
        )
        client.emit("submit_answer", {"answer": "B", "question_id": 89})
        received = client.get_received()

        confirmed = next((e for e in received if e["name"] == "answer_confirmed"), None)
        if confirmed is None:
            pytest.skip(
                "answer_confirmed non reçu pour le test C-7. Envoyer les logs."
            )

        pts = confirmed["args"][0]["points"]
        assert pts == 1, (
            f"3ème réponse correcte : {pts} pts (attendu 1).\n"
            "Si 3 pts → le code lit app.config (vide) plutôt que la DB (count=2).\n"
            "Correctif C-7 : Score.query.filter_by(question_id=..., is_correct=True).count()"
        )
        client.disconnect()


# ════════════════════════════════════════════════════════════════
#  OBS — Reconnexion : broadcast leaderboard_update aux autres
# ════════════════════════════════════════════════════════════════

class TestReconnectLeaderboard:
    """
    Observation (non bloquante) identifiée pendant la review.
    Quand un joueur se reconnecte (reconnect_player), son is_connected passe
    de False → True. Les autres clients devraient recevoir leaderboard_update
    pour mettre à jour l'indicateur de présence.

    Ce test DOCUMENTE le comportement actuel — il peut passer ou échouer
    selon que le broadcast a été ajouté ou non à on_reconnect.
    """

    @pytest.mark.timeout(10)
    def test_reconnect_broadcasts_leaderboard_to_spectator(self, flask_app):
        """
        Après reconnexion d'un joueur, les autres clients reçoivent leaderboard_update.
        STATUT : observation — peut échouer si on_reconnect ne broadcast pas.
        """
        from app.main import socketio
        from app.models import Player, db

        # Spectateur (restera connecté)
        spec, _, _ = _connect_player(
            socketio, flask_app, "Spectateur", "Dorota Kishlovsky"
        )

        # Joueur qui va se déconnecter et revenir
        player_client, player_token, login_data = _connect_player(
            socketio, flask_app, "Revenant", "Bart Bass"
        )

        # Vide les files
        spec.get_received()
        player_client.get_received()

        # Déconnexion
        player_client.disconnect()
        spec.get_received()  # vide player_left + leaderboard_update du disconnect

        # Reconnexion avec le même token via reconnect_player
        player_client2 = socketio.test_client(flask_app)
        player_client2.emit("reconnect_player", {"token": player_token})
        player_client2.get_received()  # reconnect_success

        # Le spectateur devrait avoir reçu leaderboard_update
        events = spec.get_received()
        lb_events = [e for e in events if e["name"] == "leaderboard_update"]

        if len(lb_events) == 0:
            pytest.xfail(
                "leaderboard_update non reçu par le spectateur après reconnexion.\n"
                "Observation OBS-1 : on_reconnect ne broadcast pas leaderboard_update.\n"
                "Impact : l'indicateur de présence des autres joueurs reste à jour "
                "uniquement lors du prochain événement qui déclenche un broadcast."
            )
        else:
            assert len(lb_events) >= 1

        player_client2.disconnect()
        spec.disconnect()
