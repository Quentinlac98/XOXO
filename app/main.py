# ============================================================
#  GOSSIP GIRL PARTY — main.py
#  Flask + SocketIO + Routes + Logique métier
#
#  Machine à états stricte : LOBBY → QUIZ → LIBRE → QUIZ → …
#  Timers non-bloquants via APScheduler (BackgroundScheduler)
#  XOXO, Chuck Bass
# ============================================================

import os
import json
import secrets
import qrcode
import io
import base64
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_from_directory)
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
import apscheduler.events as aps_events

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from .models import (
    db, Player, GameSession, Score, Scoop, ActivityLog, ReservedSlot,
    STATE_LOBBY, STATE_QUIZ, STATE_QUIZ_PAUSED, STATE_LIBRE,
    get_or_create_game_session, load_questions, pick_one_per_category,
    get_leaderboard, log_activity, reset_game_session, init_db
)


# ============================================================
#  FACTORY — App Flask
# ============================================================

def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "..", "static"),
    )

    def abs_path(p):
        return os.path.join(BASE_DIR, p) if not os.path.isabs(p) else p

    db_path        = abs_path(os.getenv("DB_PATH",        "data/db/gossip.db"))
    upload_path    = abs_path(os.getenv("UPLOAD_PATH",    "data/uploads"))
    questions_path = abs_path(os.getenv("QUESTIONS_PATH", "questions/gossipgirl_qcm.jsonl"))

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(upload_path, exist_ok=True)

    app.config["SECRET_KEY"]                  = os.getenv("SECRET_KEY", "xoxo-fallback-secret")
    app.config["SQLALCHEMY_DATABASE_URI"]     = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"]          = 10 * 1024 * 1024
    app.config["UPLOAD_FOLDER"]               = upload_path
    app.config["QUESTIONS_PATH"]              = questions_path
    app.config["QUESTIONS_PER_SESSION"]       = int(os.getenv("QUESTIONS_PER_SESSION", 10))
    app.config["LIBRE_DURATION_MINUTES"]      = int(os.getenv("PHASE2_DURATION_MINUTES", 15))
    app.config["POST_DELAY"]                  = int(os.getenv("POST_DELAY_SECONDS", 3))
    app.config["ADMIN_PASSWORD"]              = os.getenv("ADMIN_PASSWORD", "ChuckBassGodMode2026")
    app.config["BLAIR_SECRET_CODE"]           = os.getenv("BLAIR_SECRET_CODE", "24042024")
    app.config["BLAIR_VIP_TOKEN"]             = os.getenv("BLAIR_VIP_TOKEN", "blair-vip-token")

    # ─── État quiz en mémoire ───────────────────────────────
    app.config["current_answers"]     = {}   # {sid: {is_correct, points}}
    app.config["gg_pick_candidates"]  = []   # questions proposées au GG
    app.config["gg_pick_index"]       = -1   # index en attente de pick GG (-1 = aucun)
    app.config["gg_pick_game_id"]     = None
    app.config["quiz_tick_active"]    = False  # flag pour stopper _question_tick_task
    app.config["pause_context"]       = None   # {"mode": "pick"|"question", "index": int}
    app.config["last_round_candidates"] = []   # IDs des candidates non choisies au round précédent
    app.config["turbo_mode"]            = False  # ⚡ Mode accéléré pour tests bots

    db.init_app(app)
    init_db(app)

    return app


# ============================================================
#  INIT GLOBAL
# ============================================================

app      = create_app()
socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    logger=False,
    engineio_logger=False,
)

# ─── Bot Runner — injection du socketio Flask ───────────────
from .bot_runner import orchestrator as bot_orchestrator, set_flask_socketio
set_flask_socketio(socketio, server_url=os.getenv("BOT_SERVER_URL", f"http://127.0.0.1:{os.getenv('APP_PORT', '7777')}"))

# ─── APScheduler (BackgroundScheduler) ─────────────────────
#  Utilise eventlet comme executor pour être compatible avec Flask-SocketIO
scheduler = BackgroundScheduler(
    jobstores={"default": MemoryJobStore()},
    job_defaults={"coalesce": True, "max_instances": 1},
    timezone="UTC",
)
scheduler.start()

# F6 — durée audio new_gg pour caler le délai LIBRE (évite que le Mode Libre
#       démarre pendant l'animation du nouveau GG)
NEW_GG_AUDIO_DURATION = 6.06   # secondes (durée réelle du fichier new_gg.mp3)

_questions_cache = None
_tick_task_generation = 0   # incrémenté à chaque _send_question pour invalider les ghost tasks

def get_questions():
    global _questions_cache
    if _questions_cache is None:
        _questions_cache = load_questions(app.config["QUESTIONS_PATH"])
    return _questions_cache


# ============================================================
#  DECORATORS
# ============================================================

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def vip_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("blair_vip_verified"):
            return redirect(url_for("blair_vip"))
        return f(*args, **kwargs)
    return decorated


# ============================================================
#  ROUTES PUBLIQUES
# ============================================================

@app.route("/")
def index():
    return redirect(url_for("mobile"))

@app.route("/mobile")
def mobile():
    return render_template("mobile.html")

@app.route("/projector")
def projector():
    return render_template("projector.html")

@app.route("/qr")
def qr_page():
    return render_template("qr.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "xoxo": "Gossip Girl"}), 200


# ============================================================
#  BLAIR VIP
# ============================================================

@app.route("/blair-vip")
def blair_vip():
    token = request.args.get("token", "")
    if token == app.config["BLAIR_VIP_TOKEN"]:
        session["blair_vip_verified"] = True
        return redirect(url_for("vip_page"))
    return redirect(url_for("mobile"))


@app.route("/vip")
@vip_required
def vip_page():
    return render_template("vip.html")


VIP_SOUNDS = [
    {"id": "champagne", "name": "Champagne", "icon": "🥂"},
    {"id": "drama",     "name": "Drama",     "icon": "🎭"},
    {"id": "gossip",    "name": "Gossip",    "icon": "🤫"},
    {"id": "scandale",  "name": "Scandale",  "icon": "😱"},
    {"id": "suspens",   "name": "Suspens",   "icon": "⏱"},
    {"id": "victoire",  "name": "Victoire",  "icon": "🏆"},
]


@app.route("/api/vip/sounds")
@vip_required
def api_vip_sounds():
    sounds = []
    for s in VIP_SOUNDS:
        fpath = os.path.join(app.static_folder, "sounds", "vip", f"{s['id']}.mp3")
        url   = f"/static/sounds/vip/{s['id']}.mp3" if os.path.exists(fpath) else None
        sounds.append({**s, "url": url})
    return jsonify({"sounds": sounds})


@app.route("/api/vip/blast", methods=["POST"])
@vip_required
def api_vip_blast():
    data    = request.json or {}
    content = (data.get("content", "") or "").strip()[:300]
    if not content:
        return jsonify({"ok": False, "error": "Message vide. XOXO."})

    scoop = Scoop(
        player_id   = None,
        author_name = "Gossip Girl",
        is_official = True,
        content     = content,
    )
    db.session.add(scoop)
    db.session.commit()

    socketio.emit("new_scoop",   scoop.to_dict())
    socketio.emit("play_sound",  {"sound": "new_gg"})
    socketio.emit("vip_blast",   {"content": content, "scoop_id": scoop.id})
    log_activity("vip_blast", "Blair VIP", content[:80])
    return jsonify({"ok": True, "scoop_id": scoop.id})


@app.route("/api/vip/scoops")
@vip_required
def api_vip_scoops():
    scoops = (Scoop.query
              .filter_by(is_deleted=False)
              .order_by(Scoop.is_pinned.desc(), Scoop.created_at.desc())
              .all())
    return jsonify({"scoops": [s.to_dict() for s in scoops]})


@app.route("/api/vip/scoops/<int:scoop_id>/pin", methods=["POST"])
@vip_required
def api_vip_pin_scoop(scoop_id):
    scoop = db.session.get(Scoop, scoop_id)
    if scoop and not scoop.is_deleted:
        scoop.is_pinned = not scoop.is_pinned
        db.session.commit()
        socketio.emit("scoop_pinned", scoop.to_dict())
        log_activity("vip_pin", "Blair VIP", f"Scoop #{scoop_id} {'épinglé' if scoop.is_pinned else 'désépinglé'}")
        return jsonify({"ok": True, "is_pinned": scoop.is_pinned})
    return jsonify({"ok": False})


@app.route("/api/vip/scoops/<int:scoop_id>/delete", methods=["POST"])
@vip_required
def api_vip_delete_scoop(scoop_id):
    scoop = db.session.get(Scoop, scoop_id)
    if scoop:
        scoop.is_deleted = True
        db.session.commit()
        socketio.emit("scoop_deleted", {"scoop_id": scoop_id})
        log_activity("vip_delete", "Blair VIP", f"Scoop #{scoop_id} supprimé")
    return jsonify({"ok": True})


@app.route("/api/vip/gallery")
@vip_required
def api_vip_gallery():
    dino_dir = os.path.join(app.static_folder, "dino")
    photos = []
    if os.path.isdir(dino_dir):
        for f in sorted(os.listdir(dino_dir)):
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                name = f.rsplit('.', 1)[0].replace('-', ' ').replace('_', ' ').title()
                photos.append({"url": f"/static/dino/{f}", "name": name})
    return jsonify({"photos": photos})


# ============================================================
#  ADMIN — Chuck Mode
# ============================================================

@app.route("/xoxo-admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == app.config["ADMIN_PASSWORD"]:
            session["is_admin"] = True
            session.permanent = True
            log_activity("admin_login", "Chuck Bass", "Connexion Chuck Mode")
            return redirect(url_for("admin_dashboard"))
        error = "Mot de passe incorrect. XOXO."

    return render_template("admin_login.html", error=error)


@app.route("/xoxo-admin/dashboard")
@admin_required
def admin_dashboard():
    game      = get_or_create_game_session()
    players   = Player.query.all()
    scoops    = Scoop.query.filter_by(is_deleted=False).order_by(Scoop.created_at.desc()).limit(20).all()
    logs      = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(50).all()
    blair_slot = ReservedSlot.query.filter_by(personnage="Blair Waldorf").first()

    return render_template(
        "admin.html",
        game=game,
        players=players,
        scoops=scoops,
        logs=logs,
        blair_slot=blair_slot,
        leaderboard=get_leaderboard(),
        blair_token=app.config["BLAIR_VIP_TOKEN"],
    )


@app.route("/xoxo-admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


# ============================================================
#  API ADMIN — Actions
# ============================================================

@app.route("/api/admin/phase", methods=["POST"])
@admin_required
def api_set_phase():
    data  = request.json or {}
    phase = data.get("phase", "").upper()
    game  = get_or_create_game_session()

    if phase == STATE_QUIZ:
        _start_quiz_phase(game)
    elif phase == STATE_LIBRE:
        _start_libre_phase(game)
    elif phase == STATE_LOBBY:
        _cancel_all_timers()
        game.transition_to(STATE_LOBBY)
        game.is_paused = False
        db.session.commit()
        socketio.emit("phase_changed", {"phase": STATE_LOBBY, "gg": _get_current_gg_dict(game)})
        log_activity("force_lobby", "admin", "Retour LOBBY forcé")
    elif phase == "PAUSE":
        game.is_paused = not game.is_paused
        db.session.commit()
        socketio.emit("game_paused", {"paused": game.is_paused})
        log_activity("pause", "admin", f"Pause: {game.is_paused}")

    elif phase == "PAUSE_QUIZ":
        _pause_quiz(game)

    elif phase == "RESUME_QUIZ":
        _resume_quiz(game)

    elif phase == "STOP_QUIZ":
        _stop_quiz_with_rollback(game)

    return jsonify({"ok": True, "state": game.current_state})


@app.route("/api/admin/reload-question", methods=["POST"])
@admin_required
def api_reload_question():
    """
    Relance la question en cours avec un timer frais.
    - Annule les jobs question_timer / gg_pick_timeout
    - Ré-émet new_question + recrée le job APScheduler
    - Remet current_answers à zéro (nouvelle tentative propre)
    Utile quand une question pose problème ou qu'on veut redonner du temps.
    F2-j: utilise get_current_question() — plus de dépendance à quiz_questions.
    """
    game = get_or_create_game_session()

    if not game.is_quiz:
        return jsonify({"ok": False, "error": "Pas de quiz actif."})

    # get_current_question() lit directement questions_asked en DB
    q = game.get_current_question()
    if not q:
        return jsonify({"ok": False, "error": "Aucune question en cours à relancer."})

    _cancel_job("question_timer")
    _cancel_job("gg_pick_timeout")
    app.config["gg_pick_index"]   = -1
    app.config["current_answers"] = {}

    # Supprime les Scores existants pour cette question afin que les joueurs
    # puissent re-répondre proprement (sinon submit_answer renvoie answer_error "déjà répondu")
    Score.query.filter_by(question_id=q["id"]).delete()
    db.session.commit()

    log_activity("admin_reload", "admin", f"Relance Q{game.question_index + 1}: {q['question'][:60]}")
    # [q] = liste d'un seul élément ; index=0 car _send_question travaille sur chosen_q[0]
    _send_question(game, [q], 0)
    return jsonify({"ok": True})


@app.route("/api/admin/reset", methods=["POST"])
@admin_required
def api_reset():
    """Reset complet : vide joueurs, scores, scoops, repart en LOBBY."""
    _cancel_all_timers()
    reset_game_session()
    app.config["current_answers"]    = {}
    app.config["gg_pick_candidates"] = []
    app.config["gg_pick_index"]      = -1
    socketio.emit("session_reset", {})
    return jsonify({"ok": True, "message": "Session réinitialisée."})


@app.route("/api/admin/reset-scores", methods=["POST"])
@admin_required
def api_reset_scores():
    """
    Reset partiel : remet les scores à zéro et supprime les scoops,
    mais conserve les joueurs et leur profil.
    """
    _cancel_all_timers()

    game = get_or_create_game_session()
    game.transition_to(STATE_LOBBY)
    game.quiz_number        = 0
    game.question_index     = 0
    game.questions_asked    = "[]"
    game.libre_ends_at      = None
    game.is_paused          = False
    game.current_question_data = None
    game.question_started_at   = None

    Score.query.delete()
    Scoop.query.delete()
    Player.query.update({"score_total": 0})

    # Réassigner GG à Dan si présent, sinon au premier connecté
    dan = Player.query.filter(
        Player.personnage.ilike("%Dan%Humphrey%"),
        Player.is_connected == True
    ).first()
    first_connected = dan or Player.query.filter_by(is_connected=True).order_by(Player.id).first()
    if first_connected:
        Player.query.update({"is_gg": False}, synchronize_session=False)
        first_connected.is_gg  = True
        game.current_gg_id     = first_connected.id
    else:
        Player.query.update({"is_gg": False}, synchronize_session=False)
        game.current_gg_id = None

    db.session.commit()

    app.config["current_answers"]    = {}
    app.config["gg_pick_candidates"] = []
    app.config["gg_pick_index"]      = -1

    # keep_players=True : reset partiel — les profils joueurs sont conservés en DB,
    # donc leur token de reconnexion reste valide. Les clients NE doivent PAS purger
    # leur token (sinon ils retombent sur l'écran de login au lieu du lobby).
    socketio.emit("session_reset", {"keep_players": True})
    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
    log_activity("reset_scores", "admin", "Scores + scoops remis à zéro (joueurs conservés)")
    return jsonify({"ok": True, "message": "Scores réinitialisés."})


@app.route("/api/admin/reset-questions", methods=["POST"])
@admin_required
def api_reset_questions():
    """F3 — Remet à zéro le pool de questions déjà posées sans toucher aux scores."""
    game = get_or_create_game_session()
    game.questions_asked = "[]"
    app.config["last_round_candidates"] = []
    db.session.commit()
    log_activity("reset_questions", "admin", "Pool de questions réinitialisé")
    return jsonify({"ok": True, "message": "Pool de questions réinitialisé."})



@app.route("/api/admin/refresh-clients", methods=["POST"])
@admin_required
def api_refresh_clients():
    """Force le rechargement de tous les clients mobiles connectés."""
    socketio.emit("client_reload", {})
    log_activity("refresh_clients", "admin", "Rechargement forcé de tous les clients mobiles")
    return jsonify({"ok": True})


@app.route("/api/admin/refresh-player", methods=["POST"])
@admin_required
def api_refresh_player():
    """F4 — Refresh ciblé : émet client_reload uniquement vers le session_id d'un joueur."""
    data      = request.json or {}
    player_id = data.get("player_id")
    player    = Player.query.get(player_id)
    if not player:
        return jsonify({"ok": False, "error": "Joueur introuvable."})
    if player.session_id:
        socketio.emit("client_reload", {}, to=player.session_id)
    log_activity("refresh_player", "admin", f"Reload ciblé → {player.prenom} ({player.personnage})")
    return jsonify({"ok": True})


@app.route("/api/admin/force-gg-quiz", methods=["POST"])
@admin_required
def api_force_gg_quiz():
    """F5 — Coupe le Mode Libre et envoie un signal au GG pour lancer son quiz immédiatement."""
    game = get_or_create_game_session()
    if not game.is_libre:
        return jsonify({"ok": False, "error": "Pas en Mode Libre."})

    _cancel_all_timers()
    game.transition_to(STATE_QUIZ)
    game.quiz_number   += 1
    game.question_index = 0
    game.is_paused      = False
    game.libre_ends_at  = None
    db.session.commit()

    app.config["current_answers"]    = {}
    app.config["gg_pick_index"]      = -1
    app.config["quiz_intro_pending"] = True

    gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
    if gg and gg.session_id:
        socketio.emit("gg_quiz_ready", {
            "quiz_number": game.quiz_number,
            "total":       app.config.get("QUESTIONS_PER_SESSION", 10),
        }, to=gg.session_id)

    socketio.emit("phase_changed", {
        "phase":       STATE_QUIZ,
        "quiz_number": game.quiz_number,
        "gg":          _get_current_gg_dict(game),
    })
    log_activity("force_gg_quiz", "admin", f"Mode Libre coupé → GG {gg.prenom if gg else '?'} invité à lancer")
    return jsonify({"ok": True})




@app.route("/api/admin/transfer-gg", methods=["POST"])
@admin_required
def api_transfer_gg():
    data      = request.json or {}
    player_id = data.get("player_id")
    silent    = bool(data.get("silent", False))   # True → pas de son/overlay new_gg
    game      = get_or_create_game_session()

    new_gg = Player.query.get(player_id)
    if not new_gg:
        return jsonify({"ok": False, "error": "Joueur introuvable."})

    # B1 — garde anti-doublon : bloquer si ce joueur est déjà GG
    if game.current_gg_id == new_gg.id:
        return jsonify({"ok": False, "error": "Ce joueur est déjà Gossip Girl."})

    Player.query.update({"is_gg": False}, synchronize_session=False)

    new_gg.is_gg       = True
    game.current_gg_id = new_gg.id
    db.session.commit()

    if silent:
        # Transfert silencieux : met à jour l'UI admin/mobile sans son ni overlay projecteur
        socketio.emit("gg_updated_silent", new_gg.to_dict())
    else:
        socketio.emit("new_gg", new_gg.to_dict())
        socketio.emit("play_sound", {"sound": "new_gg"})
    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
    log_activity("transfer_gg", "admin", f"→ {new_gg.prenom} ({new_gg.personnage}){' [silent]' if silent else ''}")
    return jsonify({"ok": True})


@app.route("/api/admin/delete-player", methods=["POST"])
@admin_required
def api_delete_player():
    """Suppression dure d'un joueur : Player + Scores (cascade) supprimés de la DB."""
    data      = request.json or {}
    player_id = data.get("player_id")
    player    = Player.query.get(player_id)
    if not player:
        return jsonify({"ok": False, "error": "Joueur introuvable."})

    game = get_or_create_game_session()

    # Si c'est le GG courant, libérer le slot GG
    if game.current_gg_id == player.id:
        game.current_gg_id = None

    prenom     = player.prenom
    personnage = player.personnage

    # Forcer la déconnexion côté client avant suppression
    if player.session_id:
        socketio.emit("force_logout", {}, to=player.session_id)

    db.session.delete(player)   # cascade → supprime aussi les Score associés
    db.session.commit()

    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
    log_activity("delete_player", "admin", f"{prenom} ({personnage}) supprimé définitivement")
    return jsonify({"ok": True})


@app.route("/api/admin/kick-player", methods=["POST"])
@admin_required
def api_kick_player():
    data      = request.json or {}
    player_id = data.get("player_id")
    player    = Player.query.get(player_id)
    if not player:
        return jsonify({"ok": False, "error": "Joueur introuvable."})

    player.is_connected = False
    # Invalide le token pour empêcher la reconnexion silencieuse
    import secrets as _secrets
    player.player_token = _secrets.token_urlsafe(32)  # nouveau token → ancien localStorage invalide
    db.session.commit()
    # Émet vers le sid courant ET broadcast (au cas où le sid aurait changé)
    if player.session_id:
        socketio.emit("force_logout", {}, to=player.session_id)
    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
    log_activity("kick", "admin", f"{player.prenom} expulsé (token invalidé)")
    return jsonify({"ok": True})


@app.route("/api/admin/free-blair", methods=["POST"])
@admin_required
def api_free_blair():
    blair = Player.query.filter(Player.personnage.ilike("%Blair%Waldorf%")).first()
    if blair:
        blair.is_connected = False
        db.session.commit()
        socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
        log_activity("free_blair", "admin", "Slot Blair libéré")
    return jsonify({"ok": True})


@app.route("/api/admin/delete-scoop", methods=["POST"])
@admin_required
def api_delete_scoop():
    data  = request.json or {}
    scoop = Scoop.query.get(data.get("scoop_id"))
    if scoop:
        scoop.is_deleted = True
        db.session.commit()
        socketio.emit("scoop_deleted", {"scoop_id": scoop.id})
    return jsonify({"ok": True})


@app.route("/api/admin/pin-scoop", methods=["POST"])
@admin_required
def api_pin_scoop():
    data  = request.json or {}
    scoop = Scoop.query.get(data.get("scoop_id"))
    if scoop:
        scoop.is_pinned = not scoop.is_pinned
        db.session.commit()
        socketio.emit("scoop_pinned", scoop.to_dict())
    return jsonify({"ok": True})


@app.route("/api/admin/post-as-gg", methods=["POST"])
@admin_required
def api_post_as_gg():
    """Permet à l'admin (Chuck Mode) de publier un scoop en tant que Gossip Girl,
    même sans personnage joueur associé (is_admin=True, player_id=None)."""
    data    = request.json or {}
    content = (data.get("content", "") or "").strip()[:500]
    if not content:
        return jsonify({"ok": False, "error": "Contenu vide."})

    scoop = Scoop(
        player_id   = None,        # L'admin n'a pas de joueur
        author_name = "Gossip Girl",
        is_official = True,
        content     = content,
        image_path  = data.get("image_path", None),
    )
    db.session.add(scoop)
    db.session.commit()

    socketio.emit("admin_new_scoop", {
        "scoop_id":    scoop.id,
        "author_name": "Gossip Girl",
        "content":     content[:80],
        "state":       get_or_create_game_session().current_state,
    })
    _broadcast_scoop(scoop)
    log_activity("admin_scoop", "Chuck Bass", content[:60])
    return jsonify({"ok": True, "message": "Scoop GG publié. XOXO."})


@app.route("/api/admin/custom-message", methods=["POST"])
@admin_required
def api_custom_message():
    data = request.json or {}
    msg  = (data.get("message", "") or "").strip()[:300]
    if msg:
        socketio.emit("projector_message", {"message": msg})
        log_activity("custom_msg", "admin", msg[:80])
    return jsonify({"ok": True})


@app.route("/api/admin/play-sound", methods=["POST"])
@admin_required
def api_play_sound():
    data  = request.json or {}
    sound = data.get("sound", "xoxo")
    socketio.emit("play_sound", {"sound": sound})
    return jsonify({"ok": True})


@app.route("/api/admin/stop-sound", methods=["POST"])
@admin_required
def api_stop_sound():
    """Arrête la piste audio en cours de lecture sur le projecteur."""
    socketio.emit("stop_sound", {})
    return jsonify({"ok": True})


@app.route("/api/admin/projector-qr", methods=["POST"])
@admin_required
def api_projector_qr():
    """Affiche / masque le QR code sur le projecteur."""
    data    = request.json or {}
    visible = data.get("visible", True)
    socketio.emit("projector_qr", {"visible": visible})
    log_activity("projector_qr", "admin", f"QR overlay → {'show' if visible else 'hide'}")
    return jsonify({"ok": True})


@app.route("/api/admin/projector-scores", methods=["POST"])
@admin_required
def api_projector_scores():
    """Affiche / masque le leaderboard en grand sur le projecteur."""
    data    = request.json or {}
    visible = data.get("visible", True)
    lb      = get_leaderboard() if visible else []
    socketio.emit("projector_scores", {"visible": visible, "leaderboard": lb})
    log_activity("projector_scores", "admin", f"Scores overlay → {'show' if visible else 'hide'}")
    return jsonify({"ok": True})


@app.route("/api/admin/projector-refresh", methods=["POST"])
@admin_required
def api_projector_refresh():
    """Force un rechargement de la page projecteur."""
    socketio.emit("projector_reload", {})
    log_activity("projector_refresh", "admin", "Reload projecteur demandé")
    return jsonify({"ok": True})


@app.route("/api/admin/projector-sound", methods=["POST"])
@admin_required
def api_projector_sound():
    """Active ou désactive le son sur le projecteur."""
    data  = request.json or {}
    muted = bool(data.get("muted", False))
    socketio.emit("projector_toggle_sound", {"muted": muted})
    log_activity("projector_sound", "admin", f"Son projecteur → {'MUET' if muted else 'ACTIF'}")
    return jsonify({"ok": True, "muted": muted})


@app.route("/api/admin/adjust-timer", methods=["POST"])
@admin_required
def api_adjust_timer():
    """
    Ajuste le timer actif :
    - En LIBRE  : décale libre_ends_at + émet timer_update
    - En QUIZ   : reporte le job APScheduler question_timer + émet question_tick
                  pour que le projecteur voie le nouveau décompte
    """
    data  = request.json or {}
    delta = int(data.get("delta", 0))
    game  = get_or_create_game_session()

    if not delta:
        return jsonify({"ok": False, "error": "delta manquant."})

    if game.is_libre and game.libre_ends_at:
        # ── Mode LIBRE : on décale la date de fin ──────────────────
        current = game.libre_ends_at
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        min_ends_at = datetime.now(timezone.utc) + timedelta(seconds=10)
        new_ends_at = max(current + timedelta(seconds=delta), min_ends_at)
        game.libre_ends_at = new_ends_at
        db.session.commit()
        socketio.emit("timer_update", {"ends_at": new_ends_at.isoformat()})
        log_activity("adjust_timer", "admin", f"LIBRE {delta:+}s → {new_ends_at.isoformat()}")

    elif game.is_quiz:
        # ── Mode QUIZ : reporte le job question_timer ──────────────
        job = scheduler.get_job("question_timer")
        if job and job.next_run_time:
            new_run = job.next_run_time + timedelta(seconds=delta)
            scheduler.reschedule_job("question_timer", trigger="date", run_date=new_run)
            # Calcule le nouveau remaining et l'émet en tant que tick
            remaining = max(0, int((new_run - datetime.now(timezone.utc)).total_seconds()))
            # F2-m: quiz_questions supprimé → get_current_question() pour le q_id
            q_current = game.get_current_question()
            q_id      = q_current["id"] if q_current else None
            socketio.emit("question_tick", {"remaining": remaining, "question_id": q_id})
            log_activity("adjust_timer", "admin", f"QUIZ +{delta}s → remaining ~{remaining}s")
        else:
            return jsonify({"ok": False, "error": "Aucun timer question actif."})

    elif not game.is_libre and not game.is_quiz:
        # B3 — feedback si aucun timer actif (ni LIBRE ni QUIZ)
        return jsonify({"ok": False, "error": "Aucun timer actif."})

    return jsonify({"ok": True})


@app.route("/api/admin/reset-timer", methods=["POST"])
@admin_required
def api_reset_timer():
    """Remet le timer Mode Libre à 15 min à partir de maintenant."""
    game = get_or_create_game_session()
    if not game.is_libre:
        return jsonify({"ok": False, "error": "Pas en Mode Libre."})
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    game.libre_ends_at = ends_at
    db.session.commit()
    socketio.emit("timer_update", {"ends_at": ends_at.isoformat()})
    log_activity("reset_timer", "admin", f"LIBRE reset → {ends_at.isoformat()}")
    return jsonify({"ok": True, "message": "Timer remis à 15 min."})


# ============================================================
#  API ADMIN — Données (GET)
# ============================================================

@app.route("/api/admin/players")
@admin_required
def api_get_players():
    players = Player.query.all()
    return jsonify({"players": [p.to_dict() for p in players]})

@app.route("/api/admin/scoops")
@admin_required
def api_get_scoops():
    scoops = Scoop.query.filter_by(is_deleted=False).order_by(Scoop.created_at.desc()).limit(30).all()
    return jsonify({"scoops": [s.to_dict() for s in scoops]})

@app.route("/api/admin/leaderboard")
@admin_required
def api_get_leaderboard():
    return jsonify({"leaderboard": get_leaderboard()})


@app.route("/api/admin/scores-history")
@admin_required
def api_scores_history():
    """
    Retourne l'historique des scores par quiz_number.
    Format : [{ quiz_number, scores: [{player_id, prenom, personnage, points}] }]
    """
    # Récupère tous les quiz_number distincts
    quiz_numbers = db.session.query(Score.quiz_number).distinct().order_by(Score.quiz_number).all()
    history = []
    for (qn,) in quiz_numbers:
        scores = (
            db.session.query(Score, Player)
            .join(Player, Score.player_id == Player.id)
            .filter(Score.quiz_number == qn)
            .all()
        )
        # Agrège par joueur pour ce quiz
        player_pts = {}
        for score, player in scores:
            pid = player.id
            if pid not in player_pts:
                player_pts[pid] = {
                    "player_id":  pid,
                    "prenom":     player.prenom,
                    "personnage": player.personnage,
                    "points":     0,
                }
            player_pts[pid]["points"] += score.points
        history.append({
            "quiz_number": qn,
            "scores":      sorted(player_pts.values(), key=lambda x: x["points"], reverse=True),
        })
    return jsonify({"history": history})


@app.route("/api/admin/adjust-score", methods=["POST"])
@admin_required
def api_adjust_score():
    """
    Modifie manuellement le score total d'un joueur.
    data = { player_id: int, score_total: int }
    """
    data      = request.json or {}
    player_id = data.get("player_id")
    new_score = data.get("score_total")
    if player_id is None or new_score is None:
        return jsonify({"ok": False, "error": "player_id et score_total requis."})
    player = Player.query.get(player_id)
    if not player:
        return jsonify({"ok": False, "error": "Joueur introuvable."})
    old_score         = player.score_total
    player.score_total = int(new_score)
    db.session.commit()
    socketio.emit("leaderboard_update", {"leaderboard": get_leaderboard()})
    log_activity("adjust_score", "admin", f"{player.prenom}: {old_score} → {new_score} pts")
    return jsonify({"ok": True})


@app.route("/api/admin/logs")
@admin_required
def api_get_logs():
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(100).all()
    return jsonify({"logs": [l.to_dict() for l in logs]})


# ============================================================
#  BOT CONTROL ROOM — Routes
# ============================================================

@app.route("/api/admin/turbo-mode", methods=["POST"])
@admin_required
def api_turbo_mode():
    """
    ⚡ Toggle le mode accéléré pour les tests bots.
    Body JSON : { "enabled": true | false }
    Réduit tous les timers de jeu à leur valeur minimale.
    """
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    app.config["turbo_mode"] = enabled
    state = "ON ⚡" if enabled else "OFF"
    log_activity("turbo_mode", "admin", f"Turbo mode → {state}")
    socketio.emit("turbo_mode_changed", {"enabled": enabled})
    return jsonify({"ok": True, "turbo_mode": enabled})


@app.route("/admin/bots/deploy", methods=["POST"])
@admin_required
def api_bots_deploy():
    """
    Déploie un nouveau bot avec le profil spécifié.
    Body JSON : { "profile": "random" | "slow" | "silent" | "crasher" }
    """
    data    = request.get_json(silent=True) or {}
    profile = data.get("profile", "random")
    result  = bot_orchestrator.deploy(profile=profile)
    if "error" in result:
        return jsonify(result), 400
    return jsonify({"ok": True, "bot": result})


@app.route("/admin/bots/<bot_id>", methods=["DELETE"])
@admin_required
def api_bots_remove(bot_id):
    """Déconnecte et supprime le bot identifié par bot_id."""
    removed = bot_orchestrator.remove(bot_id)
    if not removed:
        return jsonify({"error": "Bot introuvable."}), 404
    return jsonify({"ok": True})


@app.route("/admin/bots/remove_all", methods=["POST"])
@admin_required
def api_bots_remove_all():
    """Supprime tous les bots actifs et clôture la session de logs."""
    bot_orchestrator.remove_all()
    return jsonify({"ok": True})


@app.route("/admin/bots", methods=["GET"])
@admin_required
def api_bots_list():
    """Retourne la liste et le statut de tous les bots actifs."""
    bots = bot_orchestrator.list()
    return jsonify({
        "bots":  bots,
        "count": len(bots),
        "max":   10,
    })


@app.route("/admin/bots/logs", methods=["GET"])
@admin_required
def api_bots_logs():
    """Retourne les 100 dernières lignes de bot_logs.txt."""
    lines = bot_orchestrator.get_recent_logs(n=100)
    return jsonify({"logs": lines})


@app.route("/api/admin/game-state")
@admin_required
def api_game_state():
    game = get_or_create_game_session()
    return jsonify({
        "state":       game.current_state,
        "phase":       game.current_state,   # alias rétro-compatible
        "quiz_number": game.quiz_number,
        "is_paused":   game.is_paused,
        "gg":          _get_current_gg_dict(game),
        "leaderboard": get_leaderboard(),
        "ends_at":     (game.libre_ends_at.replace(tzinfo=timezone.utc) if game.libre_ends_at.tzinfo is None else game.libre_ends_at).isoformat() if game.libre_ends_at else None,
    })


# ============================================================
#  QR CODE
# ============================================================

@app.route("/api/scoops")
def api_scoops_public():
    """
    Route publique — récupère les N derniers scoops non supprimés.
    Utilisée par le projecteur au chargement (F5) pour reconstruire le feed.
    Mode WAL SQLite : lecture non-bloquante même si une écriture est en cours.
    """
    limit  = min(int(request.args.get("limit", 30)), 100)
    scoops = (Scoop.query
              .filter_by(is_deleted=False)
              .order_by(Scoop.created_at.asc())   # asc → le plus récent sera inséré en tête
              .limit(limit)
              .all())
    return jsonify({"scoops": [s.to_dict() for s in scoops]})


@app.route("/api/qr-code")
def api_qr_code():
    base_url   = request.host_url.rstrip("/")
    mobile_url = f"{base_url}/mobile"
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(mobile_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return jsonify({"qr_base64": b64, "url": mobile_url})


# ============================================================
#  UPLOAD IMAGE
# ============================================================

@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    if "image" not in request.files:
        return jsonify({"error": "Pas d'image."}), 400

    f   = request.files["image"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return jsonify({"error": "Format non supporté."}), 400

    fname = f"{secrets.token_hex(8)}{ext}"
    fpath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
    f.save(fpath)
    return jsonify({"image_path": f"/static/uploads/{fname}"})


# ============================================================
#  LOGIQUE MÉTIER — Helpers internes
# ============================================================

def _get_current_gg_dict(game: GameSession):
    if game.current_gg_id:
        gg = Player.query.get(game.current_gg_id)
        if gg:
            return gg.to_dict()
    return None


# ─── Turbo mode helper ─────────────────────────────────────

def _t(normal: float, turbo: float) -> float:
    """Retourne `turbo` si turbo_mode est actif, sinon `normal`.
    Utilisé sur tous les timers de jeu pour accélérer les tests bots.
    """
    return turbo if app.config.get("turbo_mode") else normal


# ─── Gestion des jobs APScheduler ──────────────────────────

def _cancel_job(job_id: str):
    """Annule un job APScheduler sans lever d'exception s'il n'existe pas."""
    job = scheduler.get_job(job_id)
    if job:
        job.remove()


def _cancel_all_timers():
    """Annule tous les timers de jeu (APScheduler jobs).
    Les background tasks eventlet ne sont pas annulables, mais leurs guards
    d'état interne (is_quiz / is_libre) les stoppent naturellement.
    """
    for jid in ("gg_pick_timeout", "question_timer", "libre_timer",
                "libre_tick", "question_tick_task", "quiz_intro_timeout"):
        _cancel_job(jid)
    # Remet le flag intro à False pour éviter un démarrage fantôme
    app.config["quiz_intro_pending"] = False


# ─── Broadcast scoop ───────────────────────────────────────

def _broadcast_scoop(scoop: Scoop):
    """Émet new_scoop après un délai non-bloquant."""
    delay      = app.config["POST_DELAY"]
    scoop_dict = scoop.to_dict()   # sérialise AVANT le background task

    def _delayed_emit():
        import eventlet
        eventlet.sleep(delay)
        socketio.emit("new_scoop", scoop_dict)
        socketio.emit("play_sound", {"sound": "xoxo"})

    socketio.start_background_task(_delayed_emit)


# ============================================================
#  STATE MACHINE — Transitions
# ============================================================

# ─── LOBBY ─────────────────────────────────────────────────

def _enter_lobby(game: GameSession, *, emit_event: bool = True):
    """
    Retour en LOBBY (fin de quiz sans passer en LIBRE, ou reset partiel).
    Le rôle GG est conservé — il n'est PAS réattribué à Dan.
    """
    _cancel_all_timers()
    game.transition_to(STATE_LOBBY)
    game.is_paused             = False
    game.libre_ends_at         = None
    game.current_question_data = None
    game.question_started_at   = None
    db.session.commit()

    if emit_event:
        socketio.emit("phase_changed", {
            "phase": STATE_LOBBY,
            "gg":    _get_current_gg_dict(game),
        })
    log_activity("enter_lobby", "system", "Retour en LOBBY")


# ─── PAUSE / RESUME / STOP QUIZ ────────────────────────────

def _pause_quiz(game: GameSession):
    """
    Met le quiz en pause :
    - Annule tous les timers APScheduler (gg_pick_timeout, question_timer)
    - Passe l'état en QUIZ_PAUSED
    - Émet quiz_paused à tous les clients
    """
    if not game.is_quiz:
        # B2 — feedback admin si aucun quiz actif
        socketio.emit("admin_error", {"message": "Aucun quiz actif à mettre en pause."})
        return

    # Stoppe le tick eventlet immédiatement
    app.config["quiz_tick_active"] = False

    # Détermine le contexte : pick GG en attente ou question active ?
    gg_pick_index = app.config.get("gg_pick_index", -1)
    if gg_pick_index >= 0:
        # On était en attente du pick GG
        pause_ctx = {"mode": "pick", "index": gg_pick_index}
    else:
        # Une question était en cours — question_index pointe sur la question active
        # (avant que _question_timer_job ne l'incrémente à index+1)
        pause_ctx = {"mode": "question", "index": game.question_index}

    app.config["pause_context"] = pause_ctx

    _cancel_all_timers()
    game.transition_to(STATE_QUIZ_PAUSED)
    game.is_paused = True
    db.session.commit()

    log_activity("quiz_paused", "admin", f"Quiz #{game.quiz_number} mis en pause — contexte: {pause_ctx}")
    socketio.emit("phase_changed", {
        "phase": STATE_QUIZ_PAUSED,
        "quiz_number": game.quiz_number,
        "question_index": game.question_index,
        "gg": _get_current_gg_dict(game),
    })
    socketio.emit("quiz_paused", {"paused": True, "question_index": game.question_index})


def _resume_quiz(game: GameSession):
    """
    F2 — Reprend le quiz là où il s'était arrêté.
    Mode question  → utilise game.get_current_question() au lieu de questions[idx]
    Mode pick      → _ask_gg_to_pick avec liste vide
    """
    if not game.is_quiz_paused:
        return

    game.transition_to(STATE_QUIZ)
    game.is_paused = False
    db.session.commit()

    pause_ctx   = app.config.get("pause_context") or {"mode": "pick", "index": game.question_index}
    pause_mode  = pause_ctx.get("mode", "pick")
    pause_index = pause_ctx.get("index", game.question_index)

    log_activity("quiz_resumed", "admin", f"Quiz #{game.quiz_number} repris — contexte: {pause_ctx}")
    socketio.emit("phase_changed", {
        "phase":       STATE_QUIZ,
        "quiz_number": game.quiz_number,
        "gg":          _get_current_gg_dict(game),
        "resumed":     True,
    })
    socketio.emit("quiz_paused", {"paused": False})

    if pause_mode == "question":
        # Était au milieu d'une question → renvoie la question courante (fresh 10s)
        q = game.get_current_question()
        if q:
            game.question_index = pause_index
            db.session.commit()
            _send_question(game, [q], 0)
        else:
            _ask_gg_to_pick(game, [], pause_index)
    else:
        # Était en attente du pick GG → relance le pick
        _ask_gg_to_pick(game, [], pause_index)

    app.config["pause_context"] = None


def _stop_quiz_with_rollback(game: GameSession):
    """
    Arrêt manuel du quiz avec rollback complet :
    1. Annule tous les timers
    2. Supprime tous les Score du quiz_number en cours
    3. Recalcule score_total de chaque joueur
    4. Retire les questions du quiz en cours de questions_asked
       (elles redeviennent disponibles pour le prochain quiz)
    5. Remet l'état en LIBRE
    6. Émet quiz_stopped + leaderboard mis à jour
    """
    if not game.is_quiz_active:
        return

    app.config["quiz_tick_active"] = False
    app.config["pause_context"]    = None
    _cancel_all_timers()

    quiz_num = game.quiz_number

    # Récupère les IDs de questions posées PENDANT ce quiz
    # (on ne peut pas les distinguer des anciens quiz sans quiz_number sur questions_asked)
    # → On retire TOUTES les questions_asked pour ce quiz_number via les Score
    question_ids_this_quiz = [
        s.question_id for s in Score.query.filter_by(quiz_number=quiz_num).all()
    ]

    # Supprime les scores du quiz courant
    Score.query.filter_by(quiz_number=quiz_num).delete()

    # Recalcule score_total pour chaque joueur
    players = Player.query.all()
    for p in players:
        p.score_total = sum(s.points for s in p.scores)

    # Retire les questions de ce quiz de questions_asked
    asked = game.get_questions_asked()
    asked_cleaned = [qid for qid in asked if qid not in question_ids_this_quiz]
    game.questions_asked = __import__("json").dumps(asked_cleaned)

    # Remet quiz_number au niveau précédent (ce quiz est annulé)
    game.quiz_number   = max(0, quiz_num - 1)
    game.question_index = 0
    game.set_current_question(None)
    game.is_paused = False

    # ── Fix GG hors-ligne après rollback ──────────────────────────────────
    # Si le GG actuel est hors-ligne, on cherche un remplaçant connecté
    # pour que la soirée puisse continuer (quelqu'un doit pouvoir lancer le quiz).
    current_gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
    if current_gg and not current_gg.is_connected:
        replacement = Player.query.filter_by(is_connected=True, is_admin=False).order_by(
            Player.score_total.desc()
        ).first()
        if replacement:
            Player.query.update({"is_gg": False}, synchronize_session=False)
            replacement.is_gg    = True
            game.current_gg_id   = replacement.id
            log_activity("gg_auto_replace", "system",
                         f"GG hors-ligne après rollback → {replacement.prenom} ({replacement.personnage})")
            socketio.emit("new_gg", replacement.to_dict())

    db.session.commit()

    leaderboard = get_leaderboard()
    log_activity("quiz_stopped", "admin", f"Quiz #{quiz_num} arrêté + rollback scores")

    socketio.emit("quiz_stopped", {
        "message": "Quiz arrêté par l'admin. Les points ont été annulés. XOXO.",
        "quiz_number": quiz_num,
    })
    socketio.emit("leaderboard_update", {"leaderboard": leaderboard})

    # Entrée propre en LIBRE : établit libre_ends_at + le job de fin + le tick serveur,
    # et émet phase_changed avec un ends_at VALIDE. Sans cela, l'admin (Chuck Mode) restait
    # figé sur --:-- et /api/admin/adjust-timer ne faisait rien (libre_ends_at était None).
    # play_sound=False : un arrêt manuel ne doit pas déclencher l'ambiance Mode Libre.
    _start_libre_phase(game, play_sound=False)


# ─── QUIZ ──────────────────────────────────────────────────

def _start_quiz_phase(game: GameSession):
    """
    Transition LOBBY → QUIZ ou LIBRE → QUIZ.
    F2 — Chantier A : ne pré-sélectionne plus rien, appelle _ask_gg_to_pick directement.

    Séquence temporelle :
    1. Émet phase_changed + play_sound(quiz_start) → projecteur affiche l'overlay Flash Quiz
    2. Émet gg_quiz_ready au GG uniquement → il voit un bouton "Je suis prêt"
    3. Si le GG clique "Je suis prêt" → _ask_gg_to_pick immédiatement
    4. Si le son se termine (≈25s) sans clic → _ask_gg_to_pick automatiquement
       via le job APScheduler "quiz_intro_timeout"
    """
    _cancel_all_timers()

    game.transition_to(STATE_QUIZ)
    game.quiz_number   += 1
    game.question_index = 0
    game.is_paused      = False
    game.libre_ends_at  = None
    db.session.commit()

    app.config["last_round_candidates"] = []  # blacklist round réinitialisée
    app.config["current_answers"]      = {}
    app.config["gg_pick_index"]        = -1
    app.config["quiz_intro_pending"]   = True   # GG n'a pas encore confirmé

    total_questions = len(get_questions())
    log_activity("phase_quiz", "system", f"Quiz #{game.quiz_number} lancé")

    socketio.emit("phase_changed", {
        "phase":        STATE_QUIZ,
        "quiz_number":  game.quiz_number,
        "total_rounds": total_questions,
        "gg":           _get_current_gg_dict(game),
    })
    socketio.emit("play_sound", {"sound": "quiz_start"})

    # Notifie le GG qu'il peut choisir dès qu'il est prêt
    gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
    if gg and gg.session_id:
        socketio.emit("gg_quiz_ready", {
            "quiz_number": game.quiz_number,
        }, to=gg.session_id)

    # Fallback : si le GG ne clique pas dans 25s (durée son + marge), on démarre auto
    # ⚡ Turbo : 3s
    QUIZ_INTRO_TIMEOUT = _t(25, 3)
    scheduler.add_job(
        func=_quiz_intro_timeout_job,
        trigger="date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=QUIZ_INTRO_TIMEOUT),
        id="quiz_intro_timeout",
        replace_existing=True,
        kwargs={"game_id": game.id},
    )


def _quiz_intro_timeout_job(game_id: int):
    """
    Appelé par APScheduler 25 s après le lancement du quiz.
    F2 — Si le GG n'a pas cliqué "Je suis prêt", démarre le pick avec liste vide.
    """
    with app.app_context():
        if not app.config.get("quiz_intro_pending", False):
            return  # GG a déjà cliqué, rien à faire
        game = GameSession.query.get(game_id)
        if not game or not game.is_quiz:
            return
        app.config["quiz_intro_pending"] = False
        log_activity("quiz_intro_timeout", "system", "GG inactif → pick auto démarré")
        _ask_gg_to_pick(game, [], 0)


def _ask_gg_to_pick(game: GameSession, _questions_unused: list, index: int):
    """
    F2 — Chantier A : sélectionne 1 question par catégorie via pick_one_per_category().
    Le paramètre _questions_unused est conservé pour compatibilité de signature
    mais n'est plus utilisé (les questions viennent du cache global + DB).

    Cas :
    - 0 candidates → pool épuisé → _end_quiz()
    - 1 candidate  → auto-pick serveur (pas d'interaction GG)
    - N candidates → émet gg_pick_question avec timeout=15s
    """
    all_questions   = get_questions()
    already_asked   = game.get_questions_asked()
    blacklisted_ids = app.config.get("last_round_candidates", [])

    # R2 — Shuffle global avant pick pour éviter les biais liés à l'ordre du fichier
    import random as _rnd
    shuffled = list(all_questions)
    _rnd.shuffle(shuffled)
    candidates = pick_one_per_category(shuffled, already_asked, blacklisted_ids)

    if not candidates:
        # Pool épuisé → fin du quiz
        _end_quiz(game)
        return

    if len(candidates) == 1:
        # Auto-pick serveur : une seule catégorie disponible
        chosen = candidates[0]
        game.question_index = index
        db.session.commit()
        log_activity("gg_autopick_single", "system",
                     f"1 candidate → auto Q{index+1}: {chosen['question'][:60]}")
        _send_question(game, [chosen], 0)
        return

    # N candidates → propose au GG
    app.config["gg_pick_candidates"] = candidates
    app.config["gg_pick_index"]      = index
    app.config["gg_pick_game_id"]    = game.id
    # Réinitialise la blacklist : elle ne s'applique qu'au round précédent
    app.config["last_round_candidates"] = []

    payload = {
        "candidates": [
            {"id": q["id"], "question": q["question"], "category": q.get("category", "")}
            for q in candidates
        ],
        "question_number": index + 1,
        "timeout":         15,
    }

    gg = Player.query.get(game.current_gg_id) if game.current_gg_id else None
    if gg and gg.session_id:
        socketio.emit("gg_pick_question", payload, to=gg.session_id)

    socketio.emit("waiting_for_gg_pick", {
        "gg":              gg.to_dict() if gg else None,
        "question_number": index + 1,
    })

    # Timer 15s APScheduler (⚡ Turbo : 2s)
    _cancel_job("gg_pick_timeout")
    scheduler.add_job(
        func=_gg_pick_timeout_job,
        trigger="date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=_t(15, 2)),
        id="gg_pick_timeout",
        replace_existing=True,
        kwargs={"game_id": game.id, "expected_index": index},
    )


def _gg_pick_timeout_job(game_id: int, expected_index: int):
    """
    F2 — Appelé par APScheduler si le GG n'a pas choisi dans les 15s.
    Auto-pick candidates[0], blackliste candidates[1:] pour le prochain round.
    """
    with app.app_context():
        if app.config.get("gg_pick_index") != expected_index:
            return  # GG a déjà choisi ou le pick a avancé

        game = GameSession.query.get(game_id)
        if not game or not game.is_quiz:
            return

        candidates = app.config.get("gg_pick_candidates", [])
        if not candidates:
            return

        # Auto-pick le premier candidat
        chosen = candidates[0]
        # Blackliste les non-choisis pour le round suivant
        app.config["last_round_candidates"] = [q["id"] for q in candidates[1:]]
        app.config["gg_pick_index"] = -1

        # Pose question_index AVANT _send_question (guard anti-doublon)
        game.question_index = expected_index
        db.session.commit()

        log_activity("gg_timeout", "system",
                     f"GG inactif → auto Q{expected_index+1}: {chosen['question'][:60]}")
        _send_question(game, [chosen], 0)


def _send_question(game: GameSession, questions: list, index: int):
    """
    F2 — Envoie la question à l'index donné (dans la liste questions) à tous les clients.

    IMPORTANT : avec le nouveau flux, questions = [chosen_q] (liste à 1 élément)
    et index = 0 (position dans cette liste). C'est game.question_index qui porte
    le vrai numéro de round (déjà posé par _ask_gg_to_pick ou _gg_pick_timeout_job).

    Le job APScheduler reçoit index=game.question_index (le vrai round) pour que
    le guard anti-doublon dans _question_timer_job fonctionne correctement.
    """
    if index >= len(questions):
        _end_quiz(game)
        return

    _cancel_job("question_timer")
    _cancel_job("gg_pick_timeout")

    q            = questions[index]
    round_number = game.question_index   # vrai numéro de round (posé avant cet appel)

    # Ne re-pose pas question_index (déjà à jour), mais persiste la question courante
    game.set_current_question(q)
    game.question_started_at = datetime.now(timezone.utc)
    game.add_question_asked(q["id"])
    db.session.commit()

    app.config["current_answers"] = {}
    app.config["gg_pick_index"]   = -1

    socketio.emit("new_question", {
        "question_number": round_number + 1,
        "total":           app.config.get("QUESTIONS_PER_SESSION", 10),
        "question_id":     q["id"],
        "question":        q["question"],
        "choices":         q["choices"],
        "category":        q.get("category", ""),
        "duration":        10,
        "answer":          q.get("answer", ""),   # lettre correcte (A/B/C/D) pour Chuck Mode
    })

    log_activity("question", "system", f"Q{round_number + 1}: {q['question'][:60]}")

    # Timer 10s — kwargs reçoit le VRAI round_number comme index
    # (guard anti-doublon dans _question_timer_job compare game.question_index != index)
    # ⚡ Turbo : 3s
    question_duration = int(_t(10, 3))
    scheduler.add_job(
        func=_question_timer_job,
        trigger="date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=question_duration),
        id="question_timer",
        replace_existing=True,
        kwargs={"game_id": game.id, "question_id": q["id"], "index": round_number},
    )

    global _tick_task_generation
    _tick_task_generation += 1
    app.config["quiz_tick_active"] = True
    socketio.start_background_task(_question_tick_task, q["id"], question_duration, _tick_task_generation)


def _question_tick_task(question_id: int, duration: int, task_gen: int):
    """
    Émet question_tick chaque seconde.
    Le remaining est calculé dynamiquement depuis next_run_time du job APScheduler,
    ce qui garantit la cohérence même après un +10s / -5s (reschedule).
    Si le job n'existe plus, on se base sur le dernier remaining connu jusqu'à 0.
    task_gen : génération de la tâche — si _tick_task_generation a avancé, on est
    un ghost task et on s'arrête immédiatement pour éviter les doubles émissions.
    """
    import eventlet
    last_remaining = duration
    while True:
        if _tick_task_generation != task_gen:
            return  # Ghost task : une nouvelle question a été envoyée, on s'arrête
        if not app.config.get("quiz_tick_active", True):
            return  # Pause ou arrêt — on stoppe proprement

        # Calcul dynamique depuis APScheduler (source de vérité)
        try:
            job = scheduler.get_job("question_timer")
            if job and job.next_run_time:
                remaining = max(0, int((job.next_run_time - datetime.now(timezone.utc)).total_seconds()))
            else:
                # Job terminé ou absent : on décrémente le dernier connu
                remaining = max(0, last_remaining - 1)
        except Exception:
            remaining = max(0, last_remaining - 1)

        socketio.emit("question_tick", {"remaining": remaining, "question_id": question_id})
        last_remaining = remaining

        if remaining <= 0:
            return  # Timer écoulé, on laisse APScheduler déclencher la suite

        eventlet.sleep(1)


def _question_timer_job(game_id: int, question_id: int, index: int):
    """
    F2 — Appelé par APScheduler après 10s ou par All-In.
    Utilise game.get_current_question() au lieu de quiz_questions[index].
    Guard anti-doublon : game.question_index != index → on abandonne.
    """
    with app.app_context():
        game = GameSession.query.get(game_id)
        if not game or not game.is_quiz:
            return

        # Guard anti-doublon (index = vrai round_number passé par _send_question)
        if game.question_index != index:
            return

        q = game.get_current_question()
        if not q:
            _end_quiz(game)
            return

        scores = _get_question_scores(q["id"])

        # Avance question_index → bloque double déclenchement
        next_index = index + 1
        game.question_index = next_index
        db.session.commit()

        socketio.emit("question_answer", {
            "question_id": q["id"],
            "answer":      q["answer"],
            "explanation": q.get("explanation", ""),
            "scores":      scores,
        })

        # Guard QUESTIONS_PER_SESSION : fin du quiz si le quota est atteint
        max_questions = app.config.get("QUESTIONS_PER_SESSION", 10)
        if next_index >= max_questions:
            _delay_end = _t(5, 1)   # ⚡ Turbo : 1s
            def _delayed_end(gid, delay):
                import eventlet
                eventlet.sleep(delay)
                with app.app_context():
                    g = GameSession.query.get(gid)
                    if g and g.is_quiz:
                        _end_quiz(g)
            socketio.start_background_task(_delayed_end, game_id, _delay_end)
            return

    # Pause 5s hors contexte Flask, puis pick GG suivant (⚡ Turbo : 1s)
    _delay_pick = _t(5, 1)
    def _after_answer(gid, nidx, delay):
        import eventlet
        eventlet.sleep(delay)
        with app.app_context():
            g = GameSession.query.get(gid)
            if g and g.is_quiz:
                _ask_gg_to_pick(g, [], nidx)

    socketio.start_background_task(_after_answer, game_id, next_index, _delay_pick)


def _end_quiz(game: GameSession):
    """
    Fin du quiz (10 questions épuisées) :
    1. Calcule le podium de la session
    2. Nomme le high-scorer nouveau GG
    3. Transition automatique → LIBRE après 5 s
    """
    leaderboard = get_leaderboard()
    winner      = leaderboard[0] if leaderboard else None

    if winner:
        Player.query.update({"is_gg": False}, synchronize_session=False)
        new_gg = Player.query.get(winner["id"])
        if new_gg:
            new_gg.is_gg       = True
            game.current_gg_id = new_gg.id
            db.session.commit()

            socketio.emit("quiz_ended", {
                "leaderboard": leaderboard,
                "new_gg":      winner,
            })
            # ── Émission standalone new_gg (même chemin que le transfert manuel) ──
            # Permet au mobile du gagnant de basculer en mode Maître du Jeu
            # sans F5, via le handler socket.on('new_gg', ...) déjà présent.
            socketio.emit("new_gg", new_gg.to_dict())
            socketio.emit("leaderboard_update", {"leaderboard": leaderboard})
            socketio.emit("play_sound", {"sound": "new_gg"})
            log_activity("quiz_ended", "system",
                         f"Nouveau GG: {winner['prenom']} ({winner['personnage']})")

    # Transition → LIBRE : délai calé sur la durée audio new_gg + marge
    # pour éviter que le Mode Libre démarre pendant l'animation du nouveau GG
    # ⚡ Turbo : 1s (on skip l'attente du son new_gg)
    libre_delay = _t(max(NEW_GG_AUDIO_DURATION + 1.5, 5.0), 1.0)
    _cancel_all_timers()
    game_id = game.id

    def _delayed_libre():
        import eventlet
        eventlet.sleep(libre_delay)
        with app.app_context():
            g = GameSession.query.get(game_id)
            if g and g.is_quiz:
                _start_libre_phase(g)

    socketio.start_background_task(_delayed_libre)


def _start_libre_phase(game: GameSession, play_sound: bool = True):
    """
    Transition → LIBRE.
    Lance un timer de 15 min via APScheduler + tick serveur chaque seconde.

    play_sound : joue le son d'entrée en Mode Libre. Mis à False lors d'un
    rollback admin (arrêt manuel du quiz) pour ne pas déclencher l'ambiance
    sonore sur une action de contrôle.
    """
    _cancel_all_timers()

    # ⚡ Turbo : 1 minute au lieu de la durée configurée
    duration = _t(app.config["LIBRE_DURATION_MINUTES"], 1)
    ends_at  = datetime.now(timezone.utc) + timedelta(minutes=duration)

    game.transition_to(STATE_LIBRE)
    game.libre_ends_at = ends_at
    game.is_paused     = False
    db.session.commit()

    log_activity("phase_libre", "system", f"Mode LIBRE lance — fin: {ends_at.isoformat()}")

    socketio.emit("phase_changed", {
        "phase":   STATE_LIBRE,
        "ends_at": ends_at.isoformat(),
        "gg":      _get_current_gg_dict(game),
    })
    if play_sound:
        socketio.emit("play_sound", {"sound": "phase2_start"})

    scheduler.add_job(
        func=_libre_end_job,
        trigger="date",
        run_date=ends_at,
        id="libre_timer",
        replace_existing=True,
        kwargs={"game_id": game.id},
    )

    socketio.start_background_task(_libre_tick_task, game.id, ends_at)


def _libre_tick_task(game_id: int, ends_at: datetime):
    """Émet timer_tick chaque seconde pendant le mode LIBRE.
    Vérifie l'état DB toutes les 10s seulement (au lieu de chaque seconde)
    pour limiter les requêtes SQLite inutiles (~900 req → ~90 req par session LIBRE).
    """
    import eventlet
    tick_count = 0
    while True:
        now       = datetime.now(timezone.utc)
        remaining = (ends_at - now).total_seconds()
        if remaining <= 0:
            socketio.emit("timer_tick", {"remaining": 0})
            break

        # Vérification DB allégée : toutes les 10 secondes
        if tick_count % 10 == 0:
            with app.app_context():
                g = GameSession.query.get(game_id)
                if not g or not g.is_libre:
                    break
                if g.libre_ends_at:
                    ends_at   = g.libre_ends_at
                    remaining = (ends_at - now).total_seconds()

        socketio.emit("timer_tick", {"remaining": max(0, int(remaining))})
        tick_count += 1
        eventlet.sleep(1)


def _libre_end_job(game_id: int):
    """Appelé par APScheduler à la fin du timer LIBRE. → Transition vers QUIZ."""
    with app.app_context():
        # expire_on_commit=True par défaut : on force un refresh pour éviter
        # de lire un objet SQLAlchemy stalé si l'admin a déjà changé la phase.
        game = GameSession.query.get(game_id)
        if not game:
            return
        db.session.refresh(game)
        if not game.is_libre:
            return
        log_activity("libre_ended", "system", "Timer LIBRE ecoule → QUIZ")
        socketio.emit("phase2_ended", {})
        socketio.emit("play_sound", {"sound": "timer_end"})
        _start_quiz_phase(game)


def _get_question_scores(question_id: int) -> list:
    """Retourne le top-3 des joueurs corrects pour une question, triés par response_ms."""
    rows = (
        Score.query
        .filter_by(question_id=question_id, is_correct=True)
        .order_by(Score.response_ms.asc())
        .limit(3)
        .all()
    )
    result = []
    for rank, s in enumerate(rows, start=1):
        player = Player.query.get(s.player_id)
        if player:
            result.append({
                "rank":        rank,
                "player_id":   player.id,
                "prenom":      player.prenom,
                "personnage":  player.personnage,
                "points":      s.points,
                "response_ms": s.response_ms,
            })
    return result
