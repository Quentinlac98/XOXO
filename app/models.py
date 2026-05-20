# ============================================================
#  GOSSIP GIRL PARTY — Models (SQLite via SQLAlchemy)
#  Machine à états : LOBBY | QUIZ | LIBRE
#  XOXO, Chuck Bass
# ============================================================

import json
import random
import sqlite3
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

# ============================================================
#  ÉTATS VALIDES (constantes)
# ============================================================
STATE_LOBBY       = "LOBBY"
STATE_QUIZ        = "QUIZ"
STATE_QUIZ_PAUSED = "QUIZ_PAUSED"   # Quiz en pause (timers arrêtés, scores conservés)
STATE_LIBRE       = "LIBRE"
VALID_STATES = (STATE_LOBBY, STATE_QUIZ, STATE_QUIZ_PAUSED, STATE_LIBRE)


# ============================================================
#  JOUEUR
# ============================================================
class Player(db.Model):
    __tablename__ = "players"

    id           = db.Column(db.Integer, primary_key=True)
    session_id   = db.Column(db.String(128), unique=True, nullable=True)
    player_token = db.Column(db.String(64),  unique=True, nullable=True)
    prenom       = db.Column(db.String(64),  nullable=False)
    personnage   = db.Column(db.String(64),  nullable=False)
    is_gg        = db.Column(db.Boolean, default=False)
    is_admin     = db.Column(db.Boolean, default=False)
    is_connected = db.Column(db.Boolean, default=True)
    score_total  = db.Column(db.Integer, default=0)
    joined_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    scores = db.relationship("Score", back_populates="player", cascade="all, delete-orphan")

    def to_dict(self):
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


# ============================================================
#  SESSION DE JEU (globale — une seule active à la fois)
# ============================================================
class GameSession(db.Model):
    __tablename__ = "game_sessions"

    id = db.Column(db.Integer, primary_key=True)

    # Machine à états : "LOBBY" | "QUIZ" | "QUIZ_PAUSED" | "LIBRE"
    current_state = db.Column(db.String(16), default=STATE_LOBBY, nullable=False)

    # Quiz
    current_gg_id      = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    quiz_number        = db.Column(db.Integer, default=0)
    question_index     = db.Column(db.Integer, default=0)
    questions_asked    = db.Column(db.Text, default="[]")

    # Mode LIBRE
    libre_ends_at = db.Column(db.DateTime, nullable=True)

    # Contrôle
    is_paused  = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    # Question courante sérialisée
    current_question_data = db.Column(db.Text, nullable=True)
    question_started_at   = db.Column(db.DateTime, nullable=True)

    # ── questions_asked helpers ──────────────────────────────
    def get_questions_asked(self):
        return json.loads(self.questions_asked or "[]")

    def add_question_asked(self, qid):
        asked = self.get_questions_asked()
        if qid not in asked:
            asked.append(qid)
        self.questions_asked = json.dumps(asked)

    # ── question courante helpers ────────────────────────────
    def get_current_question(self):
        if self.current_question_data:
            return json.loads(self.current_question_data)
        return None

    def set_current_question(self, q):
        self.current_question_data = json.dumps(q) if q else None

    def _ends_at_iso(self):
        if not self.libre_ends_at:
            return None
        dt = self.libre_ends_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    # ── sérialisation ────────────────────────────────────────
    def to_dict(self):
        return {
            "id":             self.id,
            "current_state":  self.current_state,
            "phase":          self.current_state,          # alias rétro-compat
            "current_gg_id":  self.current_gg_id,
            "quiz_number":    self.quiz_number,
            "question_index": self.question_index,
            "is_paused":      self.is_paused,
            "libre_ends_at":  self._ends_at_iso(),
            "phase2_ends_at": self._ends_at_iso(),
        }

    # ── machine à états ──────────────────────────────────────
    def transition_to(self, new_state):
        if new_state not in VALID_STATES:
            raise ValueError(f"Etat invalide : {new_state!r}")
        self.current_state = new_state
        self.updated_at    = datetime.now(timezone.utc)

    @property
    def is_lobby(self):
        return self.current_state == STATE_LOBBY

    @property
    def is_quiz(self):
        return self.current_state == STATE_QUIZ

    @property
    def is_quiz_paused(self):
        return self.current_state == STATE_QUIZ_PAUSED

    @property
    def is_quiz_active(self):
        """True si le quiz est en cours ou en pause (scores + questions valides)."""
        return self.current_state in (STATE_QUIZ, STATE_QUIZ_PAUSED)

    @property
    def is_libre(self):
        return self.current_state == STATE_LIBRE

    @property
    def phase(self):
        return self.current_state


# Copy the QueryProperty descriptor into GameSession.__dict__ so that
# unittest.mock.patch.object(GameSession, "query") can retrieve it via
# target.__dict__[name] without triggering __get__ (which needs an app context).
_qp = db.Model.__dict__.get("query")
if _qp is not None:
    GameSession.query = _qp


# ============================================================
#  SCORE
# ============================================================
class Score(db.Model):
    __tablename__ = "scores"

    id          = db.Column(db.Integer, primary_key=True)
    player_id   = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    quiz_number = db.Column(db.Integer, nullable=False)
    question_id = db.Column(db.Integer, nullable=False)
    answer      = db.Column(db.String(4), nullable=False)
    is_correct  = db.Column(db.Boolean, default=False)
    points      = db.Column(db.Integer, default=0)
    response_ms = db.Column(db.Integer, default=0)
    answered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    player = db.relationship("Player", back_populates="scores")

    def to_dict(self):
        return {
            "player_id":   self.player_id,
            "quiz_number": self.quiz_number,
            "question_id": self.question_id,
            "is_correct":  self.is_correct,
            "points":      self.points,
            "response_ms": self.response_ms,
        }


# ============================================================
#  SCOOP
# ============================================================
class Scoop(db.Model):
    __tablename__ = "scoops"

    id          = db.Column(db.Integer, primary_key=True)
    player_id   = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    author_name = db.Column(db.String(64), nullable=False)
    is_official = db.Column(db.Boolean, default=False)
    content     = db.Column(db.Text, nullable=True)
    image_path  = db.Column(db.String(256), nullable=True)
    is_pinned   = db.Column(db.Boolean, default=False)
    is_deleted  = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":          self.id,
            "author_name": self.author_name,
            "is_official": self.is_official,
            "content":     self.content,
            "image_path":  self.image_path,
            "is_pinned":   self.is_pinned,
            "created_at":  self.created_at.isoformat(),
        }


# ============================================================
#  LOG D'ACTIVITE
# ============================================================
class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id         = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(64), nullable=False)
    actor      = db.Column(db.String(64), nullable=True)
    detail     = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":         self.id,
            "event_type": self.event_type,
            "actor":      self.actor,
            "detail":     self.detail,
            "created_at": self.created_at.isoformat(),
        }


# ============================================================
#  RESERVED SLOT (Blair)
# ============================================================
class ReservedSlot(db.Model):
    __tablename__ = "reserved_slots"

    id         = db.Column(db.Integer, primary_key=True)
    personnage = db.Column(db.String(64), unique=True, nullable=False)
    held_by    = db.Column(db.String(64), nullable=True)
    session_id = db.Column(db.String(128), nullable=True)
    is_locked  = db.Column(db.Boolean, default=True)


# ============================================================
#  HELPERS
# ============================================================

def get_or_create_game_session():
    game = GameSession.query.order_by(GameSession.id.desc()).first()
    if not game:
        game = GameSession(current_state=STATE_LOBBY)
        db.session.add(game)
        db.session.commit()
    return game


_LETTERS = ("A", "B", "C", "D")

def _normalize_question(q: dict) -> dict:
    """
    Normalise le champ 'choices' et 'answer' pour garantir un format uniforme
    quel que soit l'origine de la question (ancienne ou nouvelle).

    Ancien format (questions 1-39) :
        choices = {"A": "texte A", "B": "texte B", "C": "texte C", "D": "texte D"}
        answer  = "B"   ← déjà une lettre, ordre déjà aléatoire

    Nouveau format (questions 40+) :
        choices = ["texte A", "texte B", "texte C", "texte D"]
        answer  = "texte A"  ← la valeur textuelle, TOUJOURS en position 0 dans le source
        → On shuffle les valeurs avant d'assigner les lettres pour casser le biais.

    Après normalisation, toutes les questions ont :
        choices = {"A": "...", "B": "...", "C": "...", "D": "..."}
        answer  = "B"  ← toujours une lettre majuscule A/B/C/D
    """
    import random as _random
    choices = q.get("choices", {})

    # Cas liste → shuffle + convertir en dict A/B/C/D
    if isinstance(choices, list):
        q = dict(q)  # copie pour ne pas muter le cache global

        # Mémoriser la valeur textuelle de la bonne réponse AVANT le shuffle
        raw_answer = q.get("answer", "")

        # Shuffle : casse le biais "bonne réponse toujours en première position"
        values = list(choices)[:4]
        _random.shuffle(values)

        choices_dict = {_LETTERS[i]: v for i, v in enumerate(values)}
        q["choices"] = choices_dict

        # Recalibrer answer : retrouver la lettre de la valeur shufflée
        if raw_answer not in _LETTERS:
            found = next(
                (k for k, v in choices_dict.items()
                 if v.strip().lower() == raw_answer.strip().lower()),
                None
            )
            if found:
                q["answer"] = found
            else:
                # Fallback : index numérique en string ("0","1"…) — rare
                try:
                    idx = int(raw_answer)
                    q["answer"] = _LETTERS[idx] if 0 <= idx < 4 else "A"
                except (ValueError, TypeError):
                    q["answer"] = "A"  # valeur par défaut sécurisée

    return q


def load_questions(path):
    questions = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    q = json.loads(line)
                    questions.append(_normalize_question(q))
    except FileNotFoundError:
        pass
    return questions


def pick_one_per_category(all_questions, already_asked, blacklisted_ids=None):
    """
    F1 — Chantier A : sélectionne 1 question par catégorie non vide à chaque round.

    Algorithme :
    1. Exclut les questions dans already_asked (déjà posées dans ce quiz ou les précédents)
    2. Groupe les candidates restantes par catégorie
    3. Pour chaque catégorie : préfère les questions hors blacklisted_ids (non choisies
       au round précédent) ; si toute la catégorie est blacklistée, pioche quand même
       dedans (fallback)
    4. Retourne 1 question par catégorie, triées alphabétiquement par catégorie

    Cas dégénérés :
    - 0 candidates → liste vide → _end_quiz() côté serveur
    - 1 candidate  → liste à 1 élément → auto-pick serveur sans interaction GG
    """
    if blacklisted_ids is None:
        blacklisted_ids = []

    asked_ids     = set(already_asked)
    blacklisted   = set(blacklisted_ids)

    # 1. Questions disponibles (non encore posées)
    available = [q for q in all_questions if q["id"] not in asked_ids]
    if not available:
        return []

    # 2. Grouper par catégorie
    by_category = {}
    for q in available:
        cat = q.get("category", "Général")
        by_category.setdefault(cat, []).append(q)

    # 3. Pour chaque catégorie, choisir 1 question (hors blacklist si possible)
    # R2 — shuffler les catégories ET chaque pool pour maximiser l'aléatoire
    categories = list(by_category.keys())
    random.shuffle(categories)           # ordre des catégories aléatoire à chaque round
    result = []
    for cat in categories:
        pool = by_category[cat]
        random.shuffle(pool)             # mélange les questions dans la catégorie
        preferred = [q for q in pool if q["id"] not in blacklisted]
        chosen = preferred[0] if preferred else pool[0]   # premier après shuffle = aléatoire
        result.append(chosen)

    return result

def get_leaderboard():
    players = Player.query.filter(
        Player.is_admin == False,
        Player.score_total >= 0
    ).order_by(Player.score_total.desc()).all()

    # Calcul du score du quiz en cours (quiz_number actuel) par joueur
    game = get_or_create_game_session()
    current_quiz = game.quiz_number if game.quiz_number and game.quiz_number > 0 else None
    quiz_scores = {}
    if current_quiz:
        rows = db.session.query(Score.player_id, db.func.sum(Score.points))\
            .filter(Score.quiz_number == current_quiz)\
            .group_by(Score.player_id).all()
        quiz_scores = {pid: pts for pid, pts in rows}

    result = []
    for p in players:
        d = p.to_dict()
        d["score_quiz"] = quiz_scores.get(p.id, 0)
        result.append(d)
    return result


def log_activity(event_type, actor=None, detail=None):
    entry = ActivityLog(event_type=event_type, actor=actor, detail=detail)
    db.session.add(entry)
    db.session.commit()


def reset_game_session():
    game = get_or_create_game_session()
    game.transition_to(STATE_LOBBY)
    game.quiz_number           = 0
    game.question_index        = 0
    game.questions_asked       = "[]"
    game.libre_ends_at         = None
    game.is_paused             = False
    game.current_question_data = None
    game.question_started_at   = None
    game.current_gg_id         = None

    Score.query.delete()
    Player.query.update({"score_total": 0, "is_gg": False}, synchronize_session=False)

    dan = Player.query.filter(
        Player.personnage.ilike("%Dan%Humphrey%"),
        Player.is_connected == True
    ).first()
    if dan:
        dan.is_gg          = True
        game.current_gg_id = dan.id

    db.session.commit()
    log_activity("reset", "system", "Session reinitialisee -> LOBBY")


def init_db(app):
    with app.app_context():
        db.create_all()
        blair_slot = ReservedSlot.query.filter_by(personnage="Blair Waldorf").first()
        if not blair_slot:
            db.session.add(ReservedSlot(personnage="Blair Waldorf", is_locked=True))
            db.session.commit()
        game = get_or_create_game_session()
        _migrate_old_states(game)
        _recover_state_after_restart(game)


def _recover_state_after_restart(game):
    """
    Après un redémarrage serveur, si la partie était en QUIZ ou LIBRE,
    on repasse en LOBBY pour éviter un état incohérent (les timers APScheduler
    sont perdus au restart, les clients sont déconnectés).
    On conserve les scores et le GG actuel.
    """
    if game.current_state in (STATE_QUIZ, STATE_LIBRE):
        old_state = game.current_state
        game.transition_to(STATE_LOBBY)
        game.libre_ends_at         = None
        game.current_question_data = None
        game.question_started_at   = None
        game.is_paused             = False
        db.session.commit()
        log_activity(
            "restart_recovery",
            "system",
            f"Redémarrage détecté — état {old_state!r} → LOBBY (scores conservés)"
        )


def _migrate_old_states(game):
    mapping = {
        "waiting":     STATE_LOBBY,
        "quiz":        STATE_QUIZ,
        "free":        STATE_LIBRE,
        "paused":      STATE_LOBBY,
        "QUIZ_PAUSED": STATE_QUIZ_PAUSED,
    }
    if game.current_state not in VALID_STATES:
        old = game.current_state
        game.current_state = mapping.get(old, STATE_LOBBY)
        db.session.commit()
        log_activity("migration", "system", f"Phase migree : {old!r} -> {game.current_state!r}")
