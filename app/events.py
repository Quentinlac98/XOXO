# ============================================================
#  GOSSIP GIRL PARTY — events.py
#  Handlers Socket.io
#  XOXO, Chuck Bass
# ============================================================

import secrets
from datetime import datetime, timezone

from flask import request, session
from flask_socketio import emit
from sqlalchemy.exc import IntegrityError

from .main import app, socketio, _cancel_job
from .bot_runner import set_admin_sid
from .models import (
    db, Player, GameSession, Score, Scoop, ActivityLog,
    STATE_LOBBY, STATE_QUIZ, STATE_LIBRE,
    get_or_create_game_session, get_leaderboard, log_activity,
    get_taken_characters
)
def _utc_iso(dt):
    """Retourne dt.isoformat() avec tzinfo=UTC garanti, ou None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


from .main import (
    _get_current_gg_dict, _broadcast_scoop, _start_quiz_phase,
    _ask_gg_to_pick, _send_question, _get_question_scores, _start_libre_phase,
    _cancel_job, _question_timer_job, _broadcast_roster, get_character,
)


# ============================================================
#  CONNEXION / DECONNEXION
# ============================================================

@socketio.on("connect")
def on_connect():
    # Envoie l'occupation courante du roster au client qui vient de se connecter,
    # pour que la grille de personnages soit à jour avant tout login.
    emit("roster_state", {"taken": get_taken_characters()})


@socketio.on("get_roster")
def on_get_roster(data=None):
    """Snapshot d'occupation à la demande (appelé par la grille mobile/vip au build)."""
    emit("roster_state", {"taken": get_taken_characters()})


@socketio.on("admin_identify")
def on_admin_identify(data=None):
    """
    Émis par l'UI Chuck à chaque connexion Socket.io.
    Permet au bot_runner de cibler cet admin pour les logs temps réel.
    """
    set_admin_sid(request.sid)


@socketio.on("disconnect")
def on_disconnect():
    sid    = request.sid
    # NB : ne pas purger `session["blair_vip_verified"]` ici — les changements
    # de session dans un handler socket avec manage_session=True restent locaux
    # au SID et ne se répercutent PAS sur le cookie HTTP. Le pop() était donc
    # sans effet réel, mais il embrouillait la lecture du flux d'auth VIP.
    player = Player.query.filter_by(session_id=sid).first()
    if player and not player.is_admin:
        player.is_connected = False
        player.session_id   = None   # libère le SID mort pour éviter les ghost emits
        db.session.commit()
        leaderboard = get_leaderboard()
        socketio.emit("player_left", {
            "prenom":     player.prenom,
            "personnage": player.personnage,
            "count":      Player.query.filter_by(is_connected=True).count(),
        })
        socketio.emit("leaderboard_update", {"leaderboard": leaderboard})
        _broadcast_roster()
        log_activity("disconnect", player.prenom, player.personnage)


# ============================================================
#  RECONNEXION — Token persistant (localStorage)
# ============================================================

@socketio.on("reconnect_player")
def on_reconnect(data):
    sid   = request.sid
    token = data.get("token", "")

    player = Player.query.filter_by(player_token=token).first() if token else None

    if player:
        old_sid             = player.session_id
        player.session_id   = sid
        player.is_connected = True
        # Le slot a pu être repris par quelqu'un d'autre pendant la déconnexion :
        # l'index unique partiel rejette alors la reconnexion.
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            emit("reconnect_failed", {"message": "Ton personnage a ete repris. Reconnecte-toi."})
            return
        _broadcast_roster()

        # Reconnecting VIP (Blair) : réaffirme le flag de session et redirige vers /vip
        # pour retrouver le 5ᵉ onglet 💎 (absent de /mobile). Le token player_token vaut
        # preuve d'authentification — Blair a déjà passé le code secret au login initial.
        char = get_character(player.personnage)
        if char and (char.get("role") == "vip" or char.get("requires_code")):
            session["blair_vip_verified"] = True
            emit("blair_vip_granted", {"redirect": "/vip", "token": player.player_token})

        game = get_or_create_game_session()
        emit("reconnect_success", {
            "player":      player.to_dict(),
            "is_gg":       player.is_gg,
            "state":       game.current_state,
            "phase":       game.current_state,
            "gg":          _get_current_gg_dict(game),
            "ends_at":     _utc_iso(game.libre_ends_at),
            "leaderboard": get_leaderboard(),
        })

        # Si quiz en cours (ou en pause) ET c'est le GG -> renvoie le pick en attente
        if game.is_quiz_active and player.is_gg:
            idx        = app.config.get("gg_pick_index", -1)
            candidates = app.config.get("gg_pick_candidates", [])
            # F2-m : quiz_questions supprimé — total = question_index + 1 (approx)
            if idx >= 0 and candidates:
                emit("gg_pick_question", {
                    "candidates":      [{"id": q["id"], "question": q["question"]}
                                        for q in candidates],
                    "question_number": idx + 1,
                    "total":           "?",
                    "timeout":         10,
                })

        log_activity("reconnect", player.prenom, f"token OK, sid: {old_sid} -> {sid}")
    else:
        emit("reconnect_failed", {"message": "Session introuvable. Reconnecte-toi."})


# ============================================================
#  HEARTBEAT
# ============================================================

@socketio.on("ping_client")
def on_ping(data=None):
    emit("pong_server", {"ts": datetime.now(timezone.utc).isoformat()})


# ============================================================
#  PROJECTEUR — Reconnexion (F5)
# ============================================================

@socketio.on("projector_reconnect")
def on_projector_reconnect(data=None):
    """
    Émis par le projecteur au connect/reconnect.
    - Toujours : ré-émet leaderboard_update (Elite Circle) quelle que soit la phase
    - Si quiz actif : ré-émet new_question ou waiting_for_gg_pick
    Permet au projecteur de se resynchroniser sans F5 multiple.
    """
    from datetime import datetime, timezone
    from .models import get_leaderboard
    game = get_or_create_game_session()

    # ── TOUJOURS ré-émettre le leaderboard (Elite Circle) ──────────────────
    # Indépendant de la phase : en LOBBY, LIBRE ou QUIZ, les joueurs (et bots)
    # doivent apparaître dans la sidebar dès que le projecteur se (re)connecte.
    emit("leaderboard_update", {"leaderboard": get_leaderboard()})

    if game.is_libre and game.libre_ends_at:
        ends_at = game.libre_ends_at
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        emit("timer_update", {"ends_at": ends_at.isoformat()})

    if not game.is_quiz_active:
        return

    # F2-m : quiz_questions supprimé — total non significatif, on utilise "?"
    pick_index = app.config.get("gg_pick_index", -1)

    if pick_index >= 0:
        # GG en train de choisir → ré-émet waiting_for_gg_pick
        gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
        emit("waiting_for_gg_pick", {
            "gg":              gg.to_dict() if gg else None,
            "question_number": pick_index + 1,
            "total":           "?",
        })
        return

    # Question en cours → ré-émet new_question + tick actuel
    q_data = game.get_current_question()
    if not q_data:
        return

    idx = max(0, game.question_index - 1)  # question_index a déjà été incrémenté dans _send_question
    # Calcule remaining à partir de question_started_at
    remaining = 10
    if game.question_started_at:
        started = game.question_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed   = (datetime.now(timezone.utc) - started).total_seconds()
        remaining = max(0, int(10 - elapsed))

    emit("new_question", {
        "question_number": idx + 1,
        "total":           "?",
        "question_id":     q_data["id"],
        "question":        q_data["question"],
        "choices":         q_data["choices"],
        "duration":        remaining,  # temps restant réel, pas 10
        "answer":          q_data.get("answer", ""),  # lettre correcte pour Chuck Mode
    })
    emit("question_tick", {"remaining": remaining, "question_id": q_data["id"]})


# ============================================================
#  ETAT DU JEU
# ============================================================

@socketio.on("get_game_state")
def on_get_game_state(data=None):
    game = get_or_create_game_session()
    emit("game_state", {
        "state":       game.current_state,
        "phase":       game.current_state,
        "quiz_number": game.quiz_number,
        "gg":          _get_current_gg_dict(game),
        "leaderboard": get_leaderboard(),
        "ends_at":     _utc_iso(game.libre_ends_at),
    })

    # Si un quiz est actif, resynchronise l'admin avec l'état courant de la question
    # (même logique que projector_reconnect — évite une card vide si l'admin charge
    # la page après que new_question a déjà été broadcasté)
    if not game.is_quiz_active:
        return

    pick_index = app.config.get("gg_pick_index", -1)

    if pick_index >= 0:
        # GG en train de choisir → waiting_for_gg_pick
        gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
        emit("waiting_for_gg_pick", {
            "gg":              gg.to_dict() if gg else None,
            "question_number": pick_index + 1,
            "total":           "?",
        })
        return

    # Question en cours → ré-émet new_question avec le remaining réel
    q_data = game.get_current_question()
    if not q_data:
        return

    remaining = 10
    if game.question_started_at:
        started = game.question_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed   = (datetime.now(timezone.utc) - started).total_seconds()
        remaining = max(0, int(10 - elapsed))

    emit("new_question", {
        "question_number": game.question_index,   # question_index = round courant (1-based après _send_question)
        "total":           app.config.get("QUESTIONS_PER_SESSION", 10),
        "question_id":     q_data["id"],
        "question":        q_data["question"],
        "choices":         q_data["choices"],
        "category":        q_data.get("category", ""),
        "duration":        remaining,
        "answer":          q_data.get("answer", ""),  # bonne réponse visible en Chuck Mode
    })


# ============================================================
#  LOGIN
# ============================================================

@socketio.on("join_player")
def on_join_player(data):
    sid        = request.sid
    prenom     = (data.get("prenom", "") or "").strip()[:32]
    personnage = (data.get("personnage", "") or "").strip()
    blair_code = data.get("blair_code", "")

    if not prenom or not personnage:
        emit("login_error", {"message": "Prenom et personnage requis."})
        return

    # Verrous pilotés par le roster centralisé (characters.json) plutôt que par
    # des tests de sous-chaîne en dur.
    char = get_character(personnage)

    if char and char.get("role") == "admin":
        emit("login_error", {"message": "Chuck Bass est en God Mode. XOXO."})
        return

    is_vip = bool(char and (char.get("role") == "vip" or char.get("requires_code")))
    if is_vip:
        if blair_code != app.config.get("BLAIR_SECRET_CODE", ""):
            emit("login_error", {"message": "Blair Waldorf necessite un code secret. XOXO."})
            return
        session["blair_vip_verified"] = True
        # blair_vip_granted émis après commit — on a besoin de player.player_token
        # pour que le client construise /vip?token=... (cf. main.py:vip_required).

    existing = Player.query.filter_by(personnage=personnage).first()
    if existing and existing.session_id != sid and existing.is_connected:
        emit("login_error", {"message": f"{personnage} est deja choisi ce soir."})
        return

    # ── Fusion sur personnage : si un profil hors-ligne existe déjà avec ce
    #    personnage, on réutilise ce profil (mise à jour du sid + reconnexion)
    #    plutôt que de créer un doublon.  Le token est conservé pour garder la
    #    continuité des scores.
    if existing and not existing.is_connected:
        # Supprimer l'éventuel profil "vide" lié au sid courant (créé par erreur)
        stale = Player.query.filter_by(session_id=sid).first()
        if stale and stale.id != existing.id:
            db.session.delete(stale)
            db.session.flush()
        player = existing
        player.session_id   = sid
        player.is_connected = True
        # Reset le flag GG : un nouveau venu reprenant un personnage offline
        # ne doit pas hériter du rôle Gossip Girl de l'ancien occupant.
        # Le bloc Dan Humphrey plus bas le réactivera si nécessaire.
        player.is_gg        = False
    else:
        player = Player.query.filter_by(session_id=sid).first()
        if not player:
            token  = secrets.token_urlsafe(32)
            player = Player(session_id=sid, player_token=token)
            db.session.add(player)

    player.prenom       = prenom
    player.personnage   = personnage
    player.is_connected = True

    game   = get_or_create_game_session()
    # Dan Humphrey = Gossip Girl original. Détection par rôle OU par nom : le roster
    # peut ne pas porter le tag de rôle (cohérent avec reset_game_session()).
    is_dan = bool(char and (char.get("role") == "first_gg"
                            or (char.get("name") == "Dan" and char.get("family") == "Humphrey")))

    # Dan hérite du rôle GG si aucun joueur actuellement connecté n'a le flag is_gg.
    # On ne se fie pas à `game.current_gg_id` seul : il peut pointer sur un profil
    # hors-ligne d'une soirée précédente (state DB persistant entre les runs).
    if is_dan:
        active_gg = Player.query.filter_by(is_gg=True, is_connected=True).first()
        if not active_gg:
            # Nettoie un éventuel flag is_gg stale sur un profil offline
            Player.query.update({"is_gg": False}, synchronize_session=False)
            player.is_gg = True
            db.session.flush()
            game.current_gg_id = player.id
            log_activity("first_gg", prenom, "Dan Humphrey = premier Gossip Girl")

    # Verrou atomique : si un autre client a réservé ce personnage entre-temps,
    # l'index unique partiel (is_connected=1) lève IntegrityError → on perd la course.
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        emit("login_error", {"message": f"{personnage} est deja choisi ce soir."})
        return

    if is_vip:
        emit("blair_vip_granted", {"redirect": "/vip", "token": player.player_token})

    emit("login_success", {
        "player":      player.to_dict(),
        "is_gg":       player.is_gg,
        "state":       game.current_state,
        "phase":       game.current_state,
        "gg":          _get_current_gg_dict(game),
        "ends_at":     _utc_iso(game.libre_ends_at),
        "leaderboard": get_leaderboard(),
    })

    lb = get_leaderboard()
    socketio.emit("player_joined", {
        "prenom":      prenom,
        "personnage":  personnage,
        "count":       Player.query.filter_by(is_connected=True).count(),
        "leaderboard": lb,   # inclus pour que le projecteur puisse renderLeaderboard immédiatement
    })
    socketio.emit("leaderboard_update", {"leaderboard": lb})
    _broadcast_roster()
    log_activity("login", prenom, personnage)


# ============================================================
#  QUIZ — GG lance le quiz
# ============================================================

@socketio.on("gg_start_quiz")
def on_gg_start_quiz(data=None):
    """
    Depuis LOBBY : le GG lance la premiere session de quiz.
    Depuis LIBRE : le GG peut couper le mode libre et relancer un quiz.
    """
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player:
        emit("error", {"message": "Tu n'es pas connecte."})
        return

    if not (player.is_gg or player.is_admin):
        emit("error", {"message": "Seul le Gossip Girl peut lancer le quiz."})
        return

    if game.is_quiz:
        emit("error", {"message": "Un quiz est deja en cours."})
        return

    if game.is_lobby or game.is_libre:
        _start_quiz_phase(game)
    else:
        emit("error", {"message": f"Impossible de lancer un quiz depuis l'etat {game.current_state}."})


# ============================================================
#  QUIZ — GG confirme qu'il est prêt (après le son d'intro)
# ============================================================

@socketio.on("gg_ready_to_pick")
def on_gg_ready_to_pick(data=None):
    """
    Émis par le mobile du GG quand il clique sur "Je suis prêt".
    Annule le timeout automatique et démarre immédiatement le pick GG.
    """
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player or not (player.is_gg or player.is_admin):
        emit("error", {"message": "Seul le GG peut confirmer."})
        return

    if not game.is_quiz:
        return

    if not app.config.get("quiz_intro_pending", False):
        return  # Déjà démarré (timeout déclenché avant)

    app.config["quiz_intro_pending"] = False
    _cancel_job("quiz_intro_timeout")

    # F2-l : quiz_questions n'est plus utilisé — on passe [] car _ask_gg_to_pick
    # appelle pick_one_per_category() directement depuis la DB
    log_activity("gg_ready", player.prenom, "GG prêt → pick Q1")
    _ask_gg_to_pick(game, [], 0)


# ============================================================
#  QUIZ — GG choisit la question
# ============================================================

@socketio.on("gg_choose_question")
def on_gg_choose_question(data):
    """
    F2-k : Le GG choisit parmi les candidats du round courant.
    - Blackliste les non-choisis dans last_round_candidates
    - Met à jour game.question_index (vrai numéro de round)
    - Appelle _send_question(game, [chosen_q], 0) — liste d'un seul élément
    """
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player or not (player.is_gg or player.is_admin):
        emit("error", {"message": "Seul le GG peut choisir la question."})
        return

    if not game.is_quiz:
        return

    chosen_id  = data.get("question_id")
    candidates = app.config.get("gg_pick_candidates", [])
    index      = app.config.get("gg_pick_index", -1)

    if index < 0:
        return  # timeout déjà déclenché

    _cancel_job("gg_pick_timeout")
    app.config["gg_pick_index"] = -1

    chosen_q = next((q for q in candidates if q["id"] == chosen_id), None)
    if not chosen_q:
        chosen_q = candidates[0] if candidates else None
    if not chosen_q:
        return

    # Blackliste les candidats non choisis pour le prochain round
    rejected_ids = [q["id"] for q in candidates if q["id"] != chosen_q["id"]]
    app.config["last_round_candidates"] = rejected_ids

    # Positionne le vrai numéro de round (game.question_index = index du round)
    game.question_index = index
    db.session.commit()

    log_activity("gg_pick", player.prenom, f"Q{index + 1}: {chosen_q['question'][:60]}")
    # [chosen_q] = liste d'un seul élément ; idx=0 pour _send_question
    _send_question(game, [chosen_q], 0)


# ============================================================
#  QUIZ — Reponse d'un joueur
# ============================================================

@socketio.on("submit_answer")
def on_submit_answer(data):
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player or not game.is_quiz:
        return

    if player.is_gg:
        emit("answer_error", {"message": "Tu es le Gossip Girl, tu choisis les questions !"})
        return

    if player.is_admin:
        return

    question_id = data.get("question_id")
    answer      = data.get("answer", "").upper()

    if answer not in ("A", "B", "C", "D"):
        return

    already = Score.query.filter_by(player_id=player.id, question_id=question_id).first()
    if already:
        emit("answer_error", {"message": "Tu as deja repondu a cette question."})
        return

    q_data  = game.get_current_question()
    now     = datetime.now(timezone.utc)
    started = game.question_started_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    response_ms = int((now - started).total_seconds() * 1000) if started else 99999
    is_correct  = bool(q_data and answer == q_data.get("answer"))

    if is_correct:
        # Lecture depuis la DB (source de vérité atomique) pour éviter la race condition
        # entre deux joueurs qui répondent simultanément (check-then-act non atomique)
        correct_count = Score.query.filter_by(
            question_id=question_id, is_correct=True
        ).count()
        if   correct_count == 0: points = 3
        elif correct_count == 1: points = 2
        elif correct_count == 2: points = 1
        else:                    points = 0
    else:
        points = 0

    score = Score(
        player_id=player.id,
        quiz_number=game.quiz_number,
        question_id=question_id,
        answer=answer,
        is_correct=is_correct,
        points=points,
        response_ms=response_ms,
    )
    db.session.add(score)
    player.score_total += points
    db.session.commit()

    if not isinstance(app.config.get("current_answers"), dict):
        app.config["current_answers"] = {}
    app.config["current_answers"][sid] = {"is_correct": is_correct, "points": points}

    emit("answer_confirmed", {
        "answer":     answer,
        "is_correct": is_correct,
        "points":     points,
    })

    # B7 — All-In : seules les réponses de joueurs encore connectés comptent
    eligible_players = Player.query.filter_by(
        is_connected=True, is_admin=False, is_gg=False
    ).all()
    total_players   = len(eligible_players)
    eligible_sids   = {p.session_id for p in eligible_players}
    current_answers = app.config.get("current_answers", {})
    total_answered  = sum(1 for s in current_answers if s in eligible_sids)

    last_score = Score.query.filter_by(question_id=question_id).order_by(Score.answered_at.desc()).first()
    latest_player_data = None
    if last_score:
        lp = Player.query.get(last_score.player_id)
        if lp:
            latest_player_data = {"prenom": lp.prenom, "personnage": lp.personnage}
    socketio.emit("answer_progress", {
        "answered":      total_answered,
        "total_players": total_players,
        "latest_player": latest_player_data,
    })
    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})

    # All-In : si tous les joueurs participants ont répondu → on n'attend pas la fin du timer
    if total_players > 0 and total_answered >= total_players:
        q_data  = game.get_current_question()
        index   = game.question_index
        game_id = game.id
        q_id    = q_data["id"] if q_data else None

        # CRITIQUE : on annule le job APScheduler AVANT de lancer le background task
        # pour éviter que les deux se déclenchent simultanément (race condition).
        # Le guard dans _question_timer_job (question_index check) protège en dernier recours.
        _cancel_job("question_timer")

        log_activity("all_in", "system",
                     f"All-In Q{index + 1} : {total_answered}/{total_players} réponses")

        def _early_end(gid, qid, idx):
            import eventlet
            # Micro-délai pour que le dernier "answer_confirmed" soit livré avant la révélation
            eventlet.sleep(0.8)
            if qid is not None:
                _question_timer_job(gid, qid, idx)

        socketio.start_background_task(_early_end, game_id, q_id, index)


# ============================================================
#  SCOOPS
#  LOBBY  : seul le GG peut poster
#  QUIZ   : personne (le scoop part en ticker sur le projecteur)
#  LIBRE  : tout le monde
# ============================================================

@socketio.on("post_scoop")
def on_post_scoop(data):
    sid    = request.sid
    player = Player.query.filter_by(session_id=sid).first()
    game   = get_or_create_game_session()

    if not player:
        emit("error", {"message": "Tu n'es pas connecte."})
        return

    # Restrictions par etat
    if game.is_quiz_active:
        # Pendant le quiz (ou pause) : le scoop part en bandeau defilant uniquement
        content = (data.get("content", "") or "").strip()[:500]
        if not content:
            emit("error", {"message": "Ton scoop est vide."})
            return
        # Persiste le scoop mais l'emets en ticker, pas en popup
        scoop = Scoop(
            player_id=player.id,
            author_name=player.prenom,
            is_official=False,
            content=content,
            image_path=data.get("image_path", None),
        )
        db.session.add(scoop)
        db.session.commit()
        emit("scoop_submitted", {"message": "Quiz en cours — ton scoop sera affiche en bandeau. XOXO."})
        socketio.emit("scoop_ticker", {
            "scoop_id":    scoop.id,
            "author_name": scoop.author_name,
            "content":     content[:120],
        })
        socketio.emit("admin_new_scoop", {
            "scoop_id":    scoop.id,
            "author_name": scoop.author_name,
            "content":     content[:80],
            "state":       game.current_state,
        })
        log_activity("scoop_ticker", player.prenom, content[:60])
        return

    if game.is_lobby:
        # En LOBBY : seul le GG (ou admin) peut publier sur le projecteur
        if not (player.is_gg or player.is_admin):
            emit("error", {
                "message": "La soiree n'a pas encore commence. Seul Gossip Girl peut parler. XOXO."
            })
            return

    # LIBRE ou LOBBY-GG : diffusion normale
    content    = (data.get("content", "") or "").strip()[:500]
    image_path = data.get("image_path", None)
    as_gg      = data.get("as_gg", False)

    if not content and not image_path:
        emit("error", {"message": "Ton scoop est vide. Manhattan merite mieux."})
        return

    if as_gg and not (player.is_gg or player.is_admin):
        as_gg = False

    scoop = Scoop(
        player_id=player.id,
        author_name="Gossip Girl" if as_gg else player.prenom,
        is_official=as_gg,
        content=content,
        image_path=image_path,
    )
    db.session.add(scoop)
    db.session.commit()

    emit("scoop_submitted", {"message": "Ton scoop est en route. XOXO."})
    socketio.emit("admin_new_scoop", {
        "scoop_id":    scoop.id,
        "author_name": scoop.author_name,
        "content":     content[:80],
        "state":       game.current_state,
    })
    # Annonce plein écran + son réservée à Gossip Girl (officiel) et à l'admin.
    # Les scoops des autres joueurs rejoignent le feed sans interrompre tout le monde.
    _broadcast_scoop(scoop, announce=(as_gg or player.is_admin))
    log_activity("scoop", player.prenom, content[:60])


# ============================================================
#  AUDIO — Synchronisation projecteur → admin
#  Le projecteur est la source de vérité.
#  Il notifie le serveur qui rediffuse à tous les clients (dont l'admin).
# ============================================================

@socketio.on("sound_started")
def on_sound_started(data):
    """
    Émis par le projecteur dès qu'un son commence à jouer.
    data = { "sound": str, "duration": float (secondes) }
    Rediffusé à tous pour que l'admin affiche la barre de progression + active Stop.
    """
    socketio.emit("sound_started", {
        "sound":    data.get("sound", ""),
        "duration": data.get("duration", 0),
    })


@socketio.on("sound_stopped")
def on_sound_stopped(data):
    """
    Émis par le projecteur quand un son se termine naturellement ou est stoppé.
    Rediffusé à tous pour que l'admin réinitialise son UI.
    """
    socketio.emit("sound_stopped", {})


@socketio.on("projector_sound_state")
def on_projector_sound_state(data):
    """
    Émis par le projecteur pour signaler son état sonore actuel (enabled ou non).
    Rediffusé à l'admin pour mettre à jour le bouton toggle son dans la sidebar.
    data = { "enabled": bool }
    """
    socketio.emit("projector_sound_state", {
        "enabled": bool(data.get("enabled", False)),
    })
