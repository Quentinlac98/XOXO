# ============================================================
#  BOT RUNNER — bot_runner.py
#  Moteur de bots de test automatisés pour XOXO Gossip Girl
#
#  Chaque bot est un client Socket.io indépendant qui simule
#  un vrai joueur selon un profil de comportement configurable.
#
#  Profils disponibles :
#    random  — répond aléatoirement, délai humain simulé (2-8s)
#    slow    — répond toujours dans les 2 dernières secondes
#    silent  — ne répond jamais (teste le cas zéro réponses)
#    crasher — se déconnecte brutalement 3-5s après la question
#
#  XOXO, Chuck Bass
# ============================================================

import os
import time
import uuid
import random
import threading
import socketio as sio_module
from datetime import datetime

# ── Pseudos disponibles (pool Gossip Girl étendu) ──────────
BOT_PSEUDOS = [
    ("Serena",   "Serena van der Woodsen"),
    ("Blair",    "Blair Waldorf"),
    ("Nate",     "Nate Archibald"),
    ("Jenny",    "Jenny Humphrey"),
    ("Eric",     "Eric van der Woodsen"),
    ("Vanessa",  "Vanessa Abrams"),
    ("Georgina", "Georgina Sparks"),
    ("Penelope", "Penelope Shafai"),
    ("Isabel",   "Isabel Coates"),
    ("Hazel",    "Hazel Williams"),
]

PROFILES = ("random", "slow", "silent", "crasher")

# ── Chemin du fichier de logs ───────────────────────────────
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE  = os.path.join(BASE_DIR, "bot_logs.txt")

MAX_BOTS  = 10

# ── Référence à socketio Flask (injectée par main.py) ──────
flask_socketio = None
admin_sid      = None   # sid de l'admin courant (pour cibler bot_log)

# ── Buffer mémoire des derniers logs (pour replay à la connexion admin) ─
_log_buffer      = []          # liste de strings (lignes déjà horodatées)
_log_buffer_max  = 200         # capacité max du buffer circulaire
_log_buffer_lock = threading.Lock()

# ── Lock global pour les accès concurrents à l'orchestrateur ─
_orch_lock = threading.Lock()


# ============================================================
#  LOGGING
# ============================================================

def _write_log(line: str):
    """Écrit une ligne dans bot_logs.txt et la bufferise en mémoire (thread-safe)."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full = f"[{ts}] {line}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full + "\n")
    except Exception:
        pass
    # Bufferisation circulaire pour replay à la connexion admin
    with _log_buffer_lock:
        _log_buffer.append(full)
        if len(_log_buffer) > _log_buffer_max:
            del _log_buffer[0]
    return full


def _write_session_header():
    """Écrit un séparateur de session dans bot_logs.txt."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "═" * 52
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{sep}\n")
        f.write(f"  SESSION {now}\n")
        f.write(f"{sep}\n")


def _write_session_footer(duration_s: float, bot_count: int, event_count: int):
    sep = "═" * 52
    mins, secs = divmod(int(duration_s), 60)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{sep}\n")
        f.write(f"  FIN SESSION — durée: {mins:02d}:{secs:02d} — "
                f"bots: {bot_count} — events: {event_count}\n")
        f.write(f"{sep}\n\n")


# ============================================================
#  BOT PLAYER
# ============================================================

class BotPlayer:
    """
    Simule un joueur humain via python-socketio (client async-free/threading).
    Chaque instance tourne dans son propre thread daemon.
    """

    def __init__(self, bot_id: str, prenom: str, personnage: str, profile: str, server_url: str):
        self.bot_id      = bot_id
        self.prenom      = f"🤖{prenom}"
        self.personnage  = personnage
        self.profile     = profile
        self.server_url  = server_url
        self.phase       = "LOBBY"
        self.answers_sent = 0
        self.events_count = 0
        self.connected   = False
        self.alive       = True       # False = demande de déco
        self._thread     = None
        self._sio        = None
        self._crash_timer = None      # threading.Timer pour crasher mode

    # ── API publique ─────────────────────────────────────────

    def start(self):
        """Lance le bot dans un thread daemon."""
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"bot-{self.bot_id[:8]}")
        self._thread.start()

    def stop(self):
        """Demande une déconnexion propre."""
        self.alive = False
        if self._crash_timer:
            self._crash_timer.cancel()
        if self._sio and self.connected:
            try:
                self._sio.disconnect()
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "bot_id":       self.bot_id,
            "prenom":       self.prenom,
            "personnage":   self.personnage,
            "profile":      self.profile,
            "phase":        self.phase,
            "answers_sent": self.answers_sent,
            "connected":    self.connected,
        }

    # ── Lifecycle interne ────────────────────────────────────

    def _run(self):
        """Boucle principale du bot (dans son thread)."""
        self._sio = sio_module.Client(
            reconnection=False,
            logger=False,
            engineio_logger=False,
        )
        self._register_handlers()
        try:
            self._sio.connect(self.server_url, transports=["websocket"])
            self._sio.wait()   # bloquant jusqu'à déconnexion
        except Exception as e:
            self._log(f"❌ Erreur connexion : {e}")
        finally:
            self.connected = False

    def _register_handlers(self):
        """Enregistre tous les handlers d'événements Socket.io."""
        sio = self._sio

        @sio.event
        def connect():
            self.connected = True
            self._log("🔌 Connecté au serveur")
            # Joindre immédiatement comme joueur
            time.sleep(random.uniform(0.3, 1.2))  # légère pause naturelle
            sio.emit("join_player", {
                "prenom":      self.prenom,
                "personnage":  self.personnage,
                "blair_code":  "",
            })

        @sio.event
        def disconnect():
            self.connected = False
            self._log("🔌 Déconnecté")

        @sio.on("login_success")
        def on_login_success(data):
            self.events_count += 1
            phase = data.get("phase", data.get("state", "LOBBY"))
            self.phase = phase
            self._log(f"✅ Login OK — phase: {phase}")

        @sio.on("login_error")
        def on_login_error(data):
            self.events_count += 1
            msg = data.get("message", "?")
            self._log(f"❌ Login refusé : {msg}")
            # Arrêt propre si le slot est déjà pris
            self.alive = False
            sio.disconnect()

        @sio.on("game_state")
        def on_game_state(data):
            self.events_count += 1
            phase = data.get("phase", data.get("state", self.phase))
            self.phase = phase
            self._log(f"📡 game_state reçu → phase: {phase}")

        @sio.on("phase_changed")
        def on_phase_changed(data):
            self.events_count += 1
            phase = data.get("phase", self.phase)
            self.phase = phase
            self._log(f"🔄 Phase → {phase}")

        @sio.on("new_question")
        def on_new_question(data):
            self.events_count += 1
            self.phase = "QUIZ"
            q_id    = data.get("question_id")
            q_num   = data.get("question_number", "?")
            total   = data.get("total", "?")
            duration = data.get("duration", 10)
            self._log(f"❓ Question {q_num}/{total} reçue (id={q_id}, {duration}s)")

            if not self.alive:
                return

            # Comportement selon le profil
            if self.profile == "silent":
                self._log(f"🔇 [silent] Ne répond pas.")
                return

            if self.profile == "crasher":
                delay = random.uniform(3.0, 5.0)
                self._log(f"💥 [crasher] Déconnexion dans {delay:.1f}s")
                self._crash_timer = threading.Timer(delay, self._brutal_crash)
                self._crash_timer.start()
                return

            if self.profile == "random":
                delay = random.uniform(2.0, 8.0)
            elif self.profile == "slow":
                delay = max(0.5, duration - random.uniform(0.5, 2.0))
            else:
                delay = random.uniform(2.0, 6.0)

            # Sécurité : ne pas dépasser la durée de la question
            delay = min(delay, duration - 0.5)

            threading.Timer(delay, self._submit_answer, args=[q_id, duration]).start()

        @sio.on("quiz_ended")
        def on_quiz_ended(data):
            self.events_count += 1
            self._log("🏆 Quiz terminé — en attente de la prochaine phase")

        @sio.on("question_tick")
        def on_question_tick(data):
            # On ne log pas chaque tick (trop verbeux) — juste en interne
            self.events_count += 1

        @sio.on("answer_confirmed")
        def on_answer_confirmed(data):
            self.events_count += 1
            correct = data.get("is_correct", False)
            pts     = data.get("points", 0)
            icon    = "✅" if correct else "❌"
            self._log(f"{icon} Réponse confirmée — points: +{pts}")

        @sio.on("answer_error")
        def on_answer_error(data):
            self.events_count += 1
            self._log(f"⚠️ Erreur réponse : {data.get('message', '?')}")

        @sio.on("force_logout")
        def on_force_logout(data):
            self.events_count += 1
            self._log("🚪 Force logout reçu par Chuck Mode")
            self.alive = False
            sio.disconnect()

        @sio.on("session_reset")
        def on_session_reset(data):
            self.events_count += 1
            self._log("🔃 Session reset — retour en LOBBY")
            self.phase = "LOBBY"
            self.answers_sent = 0

    # ── Actions ──────────────────────────────────────────────

    def _submit_answer(self, question_id, duration: int):
        """Émet submit_answer avec un choix aléatoire."""
        if not self.alive or not self.connected:
            return
        choice = random.choice(["A", "B", "C", "D"])
        try:
            self._sio.emit("submit_answer", {
                "question_id": question_id,
                "answer":      choice,
            })
            self.answers_sent += 1
            self._log(f"📝 Réponse soumise → {choice} (question_id={question_id})")
        except Exception as e:
            self._log(f"❌ Échec submit_answer : {e}")

    def _brutal_crash(self):
        """Déconnexion brutale simulée (profil crasher)."""
        if self._sio and self.connected:
            try:
                self._sio.disconnect()
            except Exception:
                pass
        self._log("💥 [crasher] Déconnexion brutale simulée")

    # ── Logging ──────────────────────────────────────────────

    def _log(self, msg: str):
        """Log horodaté dans le fichier + émission vers l'admin via Socket.io Flask."""
        tag  = f"[{self.prenom}][{self.profile}]"
        line = _write_log(f"{tag} {msg}")
        # Émet vers l'admin (si disponible)
        _emit_bot_log(line)


# ============================================================
#  BOT ORCHESTRATOR
# ============================================================

class BotOrchestrator:
    """
    Gère le cycle de vie de tous les bots actifs.
    Singleton utilisé par les routes Flask.
    """

    def __init__(self):
        self._bots = {}   # bot_id → BotPlayer
        self._pseudo_index = 0
        self._session_start = None
        self._total_events  = 0
        self._server_url    = "http://127.0.0.1:5000"

    def set_server_url(self, url: str):
        self._server_url = url

    # ── Déploiement ──────────────────────────────────────────

    def deploy(self, profile: str = "random") -> dict:
        """
        Crée et connecte un nouveau bot.
        Retourne le dict du bot créé ou une erreur.
        """
        if profile not in PROFILES:
            return {"error": f"Profil inconnu : {profile}. Valeurs : {PROFILES}"}

        with _orch_lock:
            if len(self._bots) >= MAX_BOTS:
                return {"error": f"Limite atteinte ({MAX_BOTS} bots max)."}

            # Récupère le prochain pseudo disponible
            prenom, personnage = self._next_pseudo()
            bot_id = str(uuid.uuid4())

            bot = BotPlayer(
                bot_id=bot_id,
                prenom=prenom,
                personnage=personnage,
                profile=profile,
                server_url=self._server_url,
            )
            self._bots[bot_id] = bot

            # Démarre la session de log si première fois
            if not self._session_start:
                self._session_start = time.time()
                _write_session_header()

        bot.start()
        _write_log(f"[ORCHESTRATOR] Bot déployé : {prenom} ({profile})")
        return bot.to_dict()

    def remove(self, bot_id: str) -> bool:
        """Déconnecte et supprime un bot. Retourne True si trouvé."""
        with _orch_lock:
            bot = self._bots.pop(bot_id, None)
        if bot:
            bot.stop()
            _write_log(f"[ORCHESTRATOR] Bot supprimé : {bot.prenom}")
            return True
        return False

    def remove_all(self):
        """Supprime tous les bots actifs."""
        with _orch_lock:
            bots = list(self._bots.values())
            self._bots.clear()
        for bot in bots:
            bot.stop()

        # Écrit le footer de session si une session était active
        if self._session_start:
            duration = time.time() - self._session_start
            total_events = sum(b.events_count for b in bots)
            _write_session_footer(duration, len(bots), total_events)
            self._session_start = None

        _write_log("[ORCHESTRATOR] Tous les bots supprimés")

    # ── Consultation ─────────────────────────────────────────

    def list(self) -> list:
        """Retourne la liste des bots actifs (snapshot thread-safe)."""
        with _orch_lock:
            bots = list(self._bots.values())
        # Nettoyage automatique des bots déconnectés involontairement
        dead = [b for b in bots if not b.alive and not b.connected]
        for b in dead:
            with _orch_lock:
                self._bots.pop(b.bot_id, None)
        return [b.to_dict() for b in bots if b not in dead]

    def count(self) -> int:
        with _orch_lock:
            return len(self._bots)

    def get_recent_logs(self, n: int = 50) -> list:
        """Retourne les n dernières lignes de bot_logs.txt."""
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [l.rstrip() for l in lines[-n:]]
        except FileNotFoundError:
            return []

    # ── Pseudo generator ─────────────────────────────────────

    def _next_pseudo(self) -> tuple:
        """
        Retourne le prochain pseudo disponible dans le pool.
        Tourne en boucle si tous les slots sont utilisés.
        """
        # Récupère les personnages déjà utilisés par les bots actifs
        used = {b.personnage for b in self._bots.values()}
        for i in range(len(BOT_PSEUDOS)):
            idx = (self._pseudo_index + i) % len(BOT_PSEUDOS)
            prenom, personnage = BOT_PSEUDOS[idx]
            if personnage not in used:
                self._pseudo_index = (idx + 1) % len(BOT_PSEUDOS)
                return prenom, personnage
        # Fallback : pseudo numéroté
        n = len(self._bots) + 1
        return f"Bot{n:02d}", f"Invité {n:02d}"


# ── Singleton global ────────────────────────────────────────
orchestrator = BotOrchestrator()


# ============================================================
#  ÉMISSION VERS L'ADMIN
# ============================================================

def set_flask_socketio(socketio_instance, server_url: str = "http://127.0.0.1:5000"):
    """Injecté depuis main.py après création de l'app."""
    global flask_socketio
    flask_socketio = socketio_instance
    orchestrator.set_server_url(server_url)


def set_admin_sid(sid):
    """
    Mis à jour par events.py à chaque connexion admin.
    Rejoue les derniers logs bufferisés vers ce nouveau SID.
    """
    global admin_sid
    admin_sid = sid
    # Replay du buffer vers le nouvel admin connecté
    if flask_socketio and sid:
        with _log_buffer_lock:
            buffered = list(_log_buffer)
        for line in buffered:
            try:
                flask_socketio.emit("bot_log", {"line": line}, to=sid)
            except Exception:
                pass


def _emit_bot_log(line: str):
    """
    Émet bot_log vers l'admin (ciblé par SID).
    Si admin_sid est None (admin pas encore connecté), on ne perd pas le log :
    il est déjà dans _log_buffer et sera rejoué au prochain admin_identify.
    """
    if flask_socketio and admin_sid:
        try:
            flask_socketio.emit("bot_log", {"line": line}, to=admin_sid)
        except Exception:
            pass
