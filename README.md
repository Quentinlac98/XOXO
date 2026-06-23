# 🖤 XOXO — Gossip Girl Party App `v4.8.0`

> *The one and only source into the scandalous lives of Manhattan's elite.*

Application événementielle temps réel pour soirée à thème Gossip Girl.  
Stack : **Flask · Flask-SocketIO · APScheduler · SQLite · Docker**

> **Version actuelle : v4.8.0** — 2026-06-23

---

## 🏗 Architecture

L'application repose sur une **machine à états stricte** côté serveur. Les quatre états valides (`app/models.py` → `VALID_STATES`) sont `LOBBY`, `QUIZ`, `QUIZ_PAUSED` et `LIBRE` :

```
        ┌─────────────────────────────────────────────┐
        ▼                                             │
     LOBBY ──► QUIZ ◄──► QUIZ_PAUSED                  │
                 │  (pause / resume Chuck Mode)        │
                 │                                     │
                 ▼                                     │
               LIBRE ──────────────────────────────────┘
        (fin de quiz OU rollback admin)   (timer écoulé / 🎯 Lancer Quiz)
```

| Transition | Déclencheur |
|---|---|
| `LOBBY → QUIZ` | Lancement du quiz (GG ou Chuck Mode) |
| `QUIZ ↔ QUIZ_PAUSED` | Pause / reprise depuis Chuck Mode (`PAUSE_QUIZ` / `RESUME_QUIZ`) |
| `QUIZ → LIBRE` | Pool de questions épuisé **ou** `✕ Arrêter + Rollback` admin (`STOP_QUIZ`) |
| `LIBRE → QUIZ` | Timer LIBRE écoulé **ou** `🎯 Lancer Quiz` / `force-gg-quiz` |
| `* → LOBBY` | `Retour Lobby` ou un reset (`/api/admin/reset`, `/api/admin/reset-scores`) |

> ⚠ Le **Rollback** (`STOP_QUIZ`) bascule en **LIBRE** (pas en LOBBY) : il annule les scores du quiz en cours puis arme un timer Mode Libre complet (voir [Pause / Resume / Rollback](#-pause--resume--rollback)).

- Le serveur est le **Master Clock** : les timers sont gérés par APScheduler (threads background). Toute émission Socket.io depuis ces threads est encapsulée dans `with app.app_context()`.
- **Zéro rechargement de page** : l'UI mute entièrement via événements Socket.io — changement de phase, questions, scores, scoops, blasts.
- Chaque joueur possède un **`player_token` persistent** (localStorage) pour la reconnexion transparente en cas de coupure réseau.
- **`game.question_index`** = numéro de round courant (0-based). `game.get_current_question()` retourne la question sérialisée en DB, indépendamment de toute liste en mémoire.
- **Questions à la demande** (v4.5) : plus de pré-sélection globale en début de quiz. Les candidates sont tirées round par round via `pick_one_per_category()` — 1 question par catégorie non épuisée.
- **Photos uploadées** servies via la route dédiée `/uploads/<filename>` (et son alias rétro-compat `/static/uploads/<filename>` pour les scoops persistés avant v4.8.0).

---

## 📁 Structure du Projet

```
gossip-girl-party/
│
├── app/                        # Code Python (package)
│   ├── __init__.py             # Initialisation du package
│   ├── __main__.py             # Point d'entrée : python -m app
│   ├── main.py                 # ★ Flask app factory + routes HTTP + logique métier
│   │                           #   (machine à états, timers APScheduler, routes admin/VIP/public)
│   ├── events.py               # ★ Tous les handlers Socket.io (connect, quiz, scoops…)
│   ├── models.py               # ★ SQLAlchemy : Player, GameSession, Score, Scoop,
│   │                           #   ActivityLog, ReservedSlot + helpers DB
│   └── bot_runner.py           # Bots de simulation joueurs + streaming logs temps réel → Chuck Mode
│
├── templates/                  # Jinja2 HTML (servis par Flask)
│   ├── mobile.html             # Interface invité — style iPhone / Upper East Side
│   ├── vip.html                # Interface Blair VIP — clone de mobile + overlay 4 tuiles VIP
│   ├── projector.html          # Grand écran — animations plein écran
│   ├── admin.html              # Chuck Mode — dashboard temps réel
│   ├── admin_login.html        # Page de connexion admin (mot de passe)
│   └── qr.html                 # QR Code plein écran (pour pointer les invités)
│
├── static/
│   ├── sounds/                 # Fichiers MP3 (à fournir — voir section Sons)
│   │   └── vip/                # Sons VIP Soundboard : champagne/drama/gossip/scandale/suspens/victoire.mp3
│   └── dino/                   # Photos Dino Gallery : drop *.jpg/*.png/*.webp ici
│
├── questions/
│   └── gossipgirl_qcm.jsonl    # Base de questions (format JSON Lines)
│
├── characters.json             # Roster Gossip Girl (prénom + famille + rôle GG/VIP/admin)
├── blast-presets.json          # Templates one-tap du Chuck Blast Composer (5 par défaut)
│
├── data/                       # ⚠ Créé automatiquement — persisté par Docker Volumes
│   ├── db/                     # gossip.db (SQLite)
│   └── uploads/                # Photos uploadées par les invités (servies via /uploads/<filename>)
│
├── tests/                      # Tests unitaires (pytest)
│
├── .env                        # ⚠ Ne jamais committer — copié depuis .env.example
├── .env.example                # Template de configuration (toutes les variables)
├── Dockerfile                  # Image Python 3.11-slim + dépendances système (Pillow)
├── docker-compose.yml          # Orchestration : ports, volumes nommés, healthcheck
├── Makefile                    # Raccourcis : make up / make down / make logs / make shell
├── requirements.txt            # Dépendances Python (Flask, SocketIO, APScheduler…)
└── requirements-test.txt       # Dépendances de test (pytest, pytest-flask…)
```

> **Fichiers à ignorer / à ne pas committer** (déjà dans `.gitignore`) :  
> `app/__pycache__/`, `*.pyc`, `data/`, `.env`

---

## 🎮 Déroulement d'une soirée

### 1. Lobby

Les invités se connectent sur `/mobile`, choisissent leur prénom et leur personnage Gossip Girl. Blair Waldorf est un slot VIP nécessitant le `BLAIR_SECRET_CODE` (ou le lien QR VIP). Le Gossip Girl (GG) est le maître du jeu — son rôle est assigné automatiquement à Dan Humphrey si présent au premier login, puis transféré au meilleur score en fin de quiz (ou manuellement depuis Chuck Mode).

### 2. Phase Quiz

Le Chuck Mode déclenche le quiz depuis le Dashboard. La séquence est :

```
Admin lance Quiz
  → Son "quiz_start" (22s) sur le projecteur
  → GG voit "Je suis prêt ?" sur son mobile (bouton ou auto après 25s)
  → GG clique → pick panel s'ouvre avec les candidates du round

Pour chaque round :
  → Serveur sélectionne 1 question par catégorie disponible (pick_one_per_category)
  → GG choisit parmi les candidates (15s sinon auto-pick)
  → L'interface Admin (Chuck Mode) intercepte "answer" et colore INSTANTANÉMENT la bonne réponse en vert
  → Joueurs répondent → feedback immédiat vert/rouge sur leur mobile
  → All-In si tous ont répondu avant la fin du timer → révélation projecteur anticipée
  → Fin du timer → révélation sur projecteur + leaderboard
  → Pause 5s → round suivant
```

Le quiz se termine quand le pool de questions est épuisé (toutes les catégories vides). Le meilleur score devient le nouveau GG.

### 3. Mode Libre

Après le quiz, la soirée bascule en Mode Libre. Un timer configurable (`PHASE2_DURATION_MINUTES`) tourne. Les invités peuvent poster des scoops librement. Le Chuck Mode peut ajouter du temps (+5 / +10 min) ou lancer un nouveau quiz directement.

---

## 🃏 Système de Questions

### Format JSONL

Le fichier `questions/gossipgirl_qcm.jsonl` utilise le format JSON Lines (un objet JSON par ligne) :

```json
{
  "id": 1,
  "category": "Événements & intrigues",
  "question": "Qui est considérée comme la 'It Girl' de l'Upper East Side ?",
  "choices": {"A": "Blair Waldorf", "B": "Serena van der Woodsen", "C": "Jenny Humphrey", "D": "Vanessa Abrams"},
  "answer": "B",
  "explanation": "Serena est la figure médiatique centrale dès l'épisode 1."
}
```

| Champ | Type | Description |
|---|---|---|
| `id` | int | Identifiant unique de la question |
| `category` | string | Catégorie (affichée dans le pick panel GG) |
| `question` | string | Énoncé |
| `choices` | dict `{A,B,C,D}` **ou** liste `[a,b,c,d]` | Quatre choix de réponse (les listes sont mélangées et converties en dict A/B/C/D par `_normalize_question`) |
| `answer` | string `A/B/C/D` **ou** texte | Bonne réponse — recalibrée vers une lettre `A/B/C/D` à la normalisation |
| `explanation` | string | Explication affichée après la révélation |

### Catégories disponibles

`Citations & détails cultes` · `Couples & relations` · `Culture & Style` · `Finale & twists` · `Gossip Girl (blog & révélations)` · `Lieux & institutions` · `Qui est qui` · `Événements & intrigues`

### Algorithme de sélection (`pick_one_per_category`)

À chaque round, le serveur :
1. Exclut les questions déjà posées dans ce quiz (`questions_asked` en DB)
2. Mélange la liste globale (double shuffle : global + par catégorie)
3. Groupe les restantes par catégorie
4. Sélectionne 1 question par catégorie — en évitant les candidates rejetées au round précédent (blacklist `last_round_candidates`)
5. Retourne les candidates au GG pour qu'il choisisse (timer 15s)

Si une seule candidate existe, le serveur auto-pick sans interaction GG. Si 0 candidates → fin du quiz.

> **Blacklist inter-rounds** : les candidates non choisies par le GG sont blacklistées au round suivant (`last_round_candidates` en mémoire), forçant la diversité des propositions et évitant les répétitions immédiates.

---

## 🚀 Lancement rapide

### Lancement local (Windows — sans Docker)

```powershell
# 1. Clone le projet
git clone <repo-url> gossip-girl-party
cd gossip-girl-party

# 2. Crée et active l'environnement virtuel
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Installe les dépendances
pip install -r requirements.txt

# 4. Configure l'environnement
Copy-Item .env.example .env
# → Édite .env : SECRET_KEY, ADMIN_PASSWORD, BLAIR_SECRET_CODE, BLAIR_VIP_TOKEN

# 5. Lance l'application
python -m app

# Accès (avec APP_PORT=7777 dans .env)
# Interface invités  → http://localhost:7777/mobile
# VIP Blair          → http://localhost:7777/vip  (après auth /blair-vip?token=…)
# Projecteur         → http://localhost:7777/projector
# Chuck Mode (admin) → http://localhost:7777/xoxo-admin
# QR Code            → http://localhost:7777/qr
```

### Lancement via Docker Compose (NAS / homelab)

```bash
# 1. Clone le projet
git clone <repo-url> gossip-girl-party
cd gossip-girl-party

# 2. Configure l'environnement
cp .env.example .env
# → Édite .env : SECRET_KEY, ADMIN_PASSWORD, BLAIR_SECRET_CODE, BLAIR_VIP_TOKEN

# 3. Lance avec Docker Compose
make up
# ou : docker compose up -d --build

# Accès LOCAL (avec APP_PORT=7777 dans .env)
# Interface invités  → http://192.168.0.10:7777/mobile
# VIP Blair          → http://192.168.0.10:7777/vip  (après auth /blair-vip?token=…)
# Projecteur         → http://192.168.0.10:7777/projector
# Chuck Mode (admin) → http://192.168.0.10:7777/xoxo-admin
# QR Code            → http://192.168.0.10:7777/qr

# Accès NAS (avec APP_PORT=7777 dans .env)
# Interface invités  → http://192.168.0.13:7777/mobile
# VIP Blair          → http://192.168.0.13:7777/vip  (après auth /blair-vip?token=…)
# Projecteur         → http://192.168.0.13:7777/projector
# Chuck Mode (admin) → http://192.168.0.13:7777/xoxo-admin
# QR Code            → http://192.168.0.13:7777/qr
```

---

## 🔑 Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `SECRET_KEY` | Clé de session Flask (changer absolument) | `xoxo-fallback-secret` |
| `ADMIN_PASSWORD` | Mot de passe Chuck Mode | `ChuckBassGodMode2026` |
| `BLAIR_SECRET_CODE` | Code secret pour jouer Blair Waldorf | `24042024` |
| `BLAIR_VIP_TOKEN` | Token QR VIP Blair (lien direct sans code, ouvre aussi `/vip`) | `blair-vip-token` |
| `APP_PORT` | Port unique Flask + Docker (interne = externe) | `7777` |
| `DEBUG` | Mode debug Flask | `false` |
| `QUESTIONS_PER_SESSION` | Nombre maximal de rounds par quiz | `10` |
| `PHASE2_DURATION_MINUTES` | Durée du Mode Libre en minutes (mappé sur `LIBRE_DURATION_MINUTES`) | `15` |
| `POST_DELAY_SECONDS` | Délai avant publication d'un scoop | `3` |
| `DB_PATH` | Chemin relatif vers la base SQLite | `data/db/gossip.db` |
| `UPLOAD_PATH` | Dossier de stockage des photos (servi via `/uploads/<filename>`) | `data/uploads` |
| `QUESTIONS_PATH` | Chemin vers le fichier de questions | `questions/gossipgirl_qcm.jsonl` |
| `CHARACTERS_PATH` | Chemin vers le roster Gossip Girl | `characters.json` |
| `BLAST_PRESETS_PATH` | Chemin vers les templates Chuck Blast (one-tap) | `blast-presets.json` |
| `BOT_SERVER_URL` | URL interne ciblée par les bots de test | `http://127.0.0.1:7777` |

> ⚠ **`APP_HOST` n'est pas câblé.** Bien qu'il figure dans `.env.example`, le point d'entrée (`app/__main__.py`) fixe l'hôte en dur à `0.0.0.0`. La variable est ignorée — seul `APP_PORT` est lu au lancement.

---

## 💎 VIP Mode — Blair Waldorf

### Accès

Le VIP Mode est exclusif à Blair Waldorf. L'accès se fait via le **lien QR VIP** :

```
/blair-vip?token=<BLAIR_VIP_TOKEN>
```

Ce lien :
1. Vérifie le token (`BLAIR_VIP_TOKEN`)
2. Pose `session["blair_vip_verified"] = True`
3. Redirige vers `/vip`

Toutes les routes `/vip` et `/api/vip/*` sont protégées par le décorateur `@vip_required` : sans `session["blair_vip_verified"]`, l'accès est refusé et l'utilisateur est renvoyé vers la connexion VIP.

> Depuis v4.8.0, le flag `blair_vip_verified` est **purgé automatiquement** quand le socket se déconnecte (`on_disconnect`), évitant qu'un cookie Flask reste valide indéfiniment si Blair change de téléphone ou si quelqu'un d'autre récupère l'appareil.

### Dashboard VIP — 4 tuiles

| Tuile | Panneau | Description |
|---|---|---|
| 🐱 Dino Gallery | `vip-panel-gallery` | Carrousel photo avec gestes swipe, indicateurs de points, transition fondu |
| 🔊 VIP Soundboard | `vip-panel-soundboard` | 6 boutons audio ; MP3 si disponibles, sinon tonalités Web Audio API |
| 📰 Scoops Manager | `vip-panel-scoops-manager` | Liste des scoops avec actions Épingler / Supprimer + toggle QR Code (images affichées) |
| ✨ Custom Blast | `vip-panel-blast` | Compose un message attribué à **Blair Waldorf** + animation wow plein écran sur tous les clients (mobiles, VIP, projecteur) |

### Custom Blast (depuis v4.8.0)

- Le scoop est désormais persisté sous le nom **Blair Waldorf** (`is_official=True`), au lieu de l'ancien "Gossip Girl" générique qui doublait le canal officiel.
- Le projecteur dispose d'un **overlay VIP Blast dédié** (`#vip-blast-overlay` : 💎 + "New VIP Blast" + signature `✦ Blair Waldorf ✦`), distinct du popup générique des scoops — déclenché par `socket.on('vip_blast')`.
- Plus de doublon : la route `/api/vip/blast` n'émet plus `new_scoop` (le bandeau dédié remplace le popup). Elle émet `vip_blast`, `play_sound(new_gg)` et `scoop_published` (refresh des Scoop Managers).

### Ajouter des médias (drop-in, sans modifier le code)

| Dossier | Contenu | Effet |
|---|---|---|
| `static/dino/` | `*.jpg` / `*.png` / `*.webp` | Remplace les emojis fallback dans la galerie |
| `static/sounds/vip/` | `champagne.mp3` · `drama.mp3` · `gossip.mp3` · `scandale.mp3` · `suspens.mp3` · `victoire.mp3` | Active la lecture MP3 sur le soundboard (fallback Web Audio API si absent) |

### Événements Socket.io VIP

| Événement | Direction | Description |
|---|---|---|
| `vip_blast` | Serveur → Tous | Déclenche l'overlay Blast animé sur mobiles, VIP **et projecteur** (overlay dédié) |
| `scoop_pinned` | Serveur → Tous | Scoop épinglé — refresh des Scoop Managers |
| `scoop_deleted` | Serveur → Tous | Scoop retiré du flux |
| `scoop_published` | Serveur → Tous | Nouveau scoop officiel (VIP Blast / Chuck Blast) — refresh des Scoop Managers |

---

## 🐳 Installation Docker — NAS Synology / VPS Linux

### Port — Synology DS923+ (et tout NAS Synology)

> ⚠ **Les ports `5000` et `5001` sont réservés par DSM** (interface web Synology). Ne jamais les utiliser pour un conteneur Docker sur un NAS Synology.

`APP_PORT` est la **seule variable à changer** — elle contrôle à la fois le port sur lequel Flask écoute dans le conteneur ET le port exposé sur le NAS. Le mapping Docker est symétrique :

```yaml
# docker-compose.yml
ports:
  - "${APP_PORT:-7777}:${APP_PORT:-7777}"   # externe == interne == APP_PORT
```

### Volumes — Données persistantes

```yaml
volumes:
  gossip_db:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ./data/db

  gossip_uploads:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ./data/uploads
```

> ⚠ Sur Synology, si tu préfères des **chemins absolus** :
> ```yaml
> device: /volume1/docker/gossip-girl-party/data/db
> device: /volume1/docker/gossip-girl-party/data/uploads
> ```

### Commandes utiles

```bash
make up          # Build + lancement en arrière-plan
make down        # Arrêt + suppression des conteneurs
make logs        # Logs en temps réel
make shell       # Shell bash dans le conteneur

# Backup complet des données
tar -czf gossip_backup_$(date +%Y%m%d).tar.gz data/

# Transfert NAS → VPS
scp gossip_backup_*.tar.gz user@vps:/opt/gossip-girl-party/
tar -xzf gossip_backup_*.tar.gz
docker compose up -d --build
```

---

## 🎵 Système Audio

### Principe général

Les sons sont joués **uniquement sur le Projecteur** (`/projector`). Les mobiles restent silencieux. Le Projecteur est la **source de vérité audio** — il confirme au serveur chaque démarrage et chaque arrêt de son, permettant à Chuck Mode de rester synchronisé en temps réel.

### Fichiers MP3

Dépose tes fichiers dans `static/sounds/` :

| Fichier | Durée | Déclenchement |
|---|---|---|
| `xoxo.mp3` | 5.4s | Nouveau scoop publié |
| `quiz_start.mp3` | 22.2s | Début de phase QUIZ |
| `correct.mp3` | 2.7s | Révélation de réponse |
| `new_gg.mp3` | 6.06s | Nouveau Gossip Girl désigné / Blast VIP / Chuck Blast (défaut) |
| `phase2_start.mp3` | 14.9s | Début de phase LIBRE |
| `timer_end.mp3` | 21.3s | Fin du timer LIBRE |
| `countdown.mp3` | 2.3s | Manuel uniquement (Chuck Mode) |

> La durée de `new_gg.mp3` est utilisée comme constante `NEW_GG_AUDIO_DURATION = 6.06` dans `main.py` pour synchroniser le démarrage du Mode Libre avec la fin de l'animation. Ce même son est le défaut des Blasts (VIP et Chuck).

### Architecture audio — flux complet

```
Chuck Mode clique un bouton son
  → POST /api/admin/play-sound  {sound: "xoxo"}
  → socketio.emit("play_sound", {sound})
  → Projecteur reçoit play_sound → playSound("xoxo")
  → Projecteur joue le son → socket.emit("sound_started", {sound, duration})
  → Serveur relaie sound_started à tous les clients
  → Chuck Mode reçoit sound_started → barre de progression + bouton Stop actif

Son automatique (APScheduler, transition de phase, Blast…)
  → socketio.emit("play_sound", {sound}) depuis main.py ou events.py
  → Même chemin à partir de là

Son terminé (fin naturelle via onended)
  → Projecteur : socket.emit("sound_stopped")
  → Serveur relaie → Chuck Mode reset UI

Chuck Mode clique ■ Stop
  → POST /api/admin/stop-sound
  → socketio.emit("stop_sound")
  → Projecteur : stopCurrentSound() → pause + reset → socket.emit("sound_stopped")
```

### Mute Projecteur (Chuck Mode)

Le bouton **🔇 Son OFF / 🔊 Son ON** sur la command bar Chuck Mode contrôle le mute global du projecteur :

```
Chuck Mode toggle → POST /api/admin/projector-sound  {muted: <bool>}
  → socketio.emit("projector_toggle_sound", {muted})
  → Projecteur : document.querySelectorAll('audio').forEach(el => el.muted = data.muted)
  → Projecteur confirme via socket.emit("projector_sound_state", {enabled: !muted})
```

> Le payload utilise la clé `muted` (booléen) côté client comme côté serveur. La sémantique est unifiée : seul le projecteur joue du son, et seul lui est mute/unmute.

### Unlock Audio — Overlay Bienvenue

Un overlay plein écran s'affiche au chargement de `/projector`. Au premier clic, l'`AudioContext` est créé et résumé, l'overlay disparaît en fondu. **Le projecteur doit toujours être ouvert et l'overlay cliqué avant de lancer une session.** Sans ce clic initial, tous les `audio.play()` seront rejetés silencieusement par le navigateur.

---

## 👑 Chuck Mode — Interface Admin

Accessible via `/xoxo-admin` (mot de passe défini dans `.env` : `ADMIN_PASSWORD`).

### Command bar (mobile-friendly depuis v4.8.0)

La barre de commande en haut est désormais splitée en deux clusters :

- **`.cmd-anchor`** (gauche, figé) : `☰` (menu) · brand *Chuck Mode* · **phase pill** (`#phase-pill`). Toujours visible, jamais scrollée.
- **`.cmd-scroller`** (droite, swipable sur mobile) : compteur joueurs · `Son ON/OFF` · `↻ Mobiles` · `↻ Projecteur` · `↗ Ouvrir Projecteur`. Sur mobile, un fade-mask sur le bord droit signale le swipe horizontal.

La phase pill est désormais un **bouton dropdown avec chevron `▾`** : tap → popover `#phase-menu` (Lobby / Libre / Pause), la phase active est marquée en or. Remplace l'ancien trio de boutons inline et libère de la place dans la barre.

### Contrôle des Phases

Depuis le popover phase pill (ou la section Live) :

| Action | Effet |
|---|---|
| **Forcer Quiz** | Bascule immédiatement en phase QUIZ depuis n'importe quel état |
| **Forcer Mode Libre** | Bascule en phase LIBRE (Mode Libre social) |
| **Retour Lobby** | Remet l'application en LOBBY, scores conservés |
| **🎯 Lancer Quiz** | Lance le quiz directement depuis le Mode Libre (identique à l'action GG) |

### ⏸ Pause / Resume / Rollback

La **Pause** gère deux contextes distincts automatiquement :

- **Pause en Pick GG** : suspend le timer de 15s pendant lequel le GG choisit sa question.
- **Pause en Question** : suspend le compte à rebours de réponse. Les mobiles voient un overlay de pause.

Le serveur passe dans l'état `STATE_QUIZ_PAUSED` et émet `quiz_paused` à tous les clients.

Le **Rollback** (`✕ Arrêter + Rollback`, `STOP_QUIZ`) annule le quiz en cours sans corrompre les scores des quiz précédents :

1. Annule tous les timers, supprime les `Score` du `quiz_number` courant et recalcule chaque `score_total`.
2. Retire les questions de ce quiz de `questions_asked` (elles redeviennent disponibles).
3. Réassigne le rôle GG si le GG courant est hors-ligne.
4. **Bascule en LIBRE via `_start_libre_phase()`** — un timer Mode Libre complet est armé (`libre_ends_at`, job de fin APScheduler, tick serveur), et `phase_changed` est émis avec un `ends_at` valide. Le son d'entrée n'est pas joué (`play_sound=False`).

> Le rollback produit donc un état LIBRE pleinement formé : Chuck Mode, mobiles et projecteur affichent immédiatement le même décompte, et les boutons `-5 / +5 / +10 min` sont opérationnels sans avoir à cliquer `↺ Reset 15 min` au préalable.

### Gestion des Joueurs

- Vue temps réel des joueurs connectés (ID + Socket ID + personnage + score)
- **Expulser** un joueur (disconnect forcé + invalidation du token localStorage)
- **Supprimer** un joueur (irréversible — supprime Player + Scores en cascade)
- **↻ Refresh ciblé** — recharge le mobile d'un joueur spécifique sans affecter les autres
- **Libérer le slot Blair** — réinitialise `is_connected` pour permettre une nouvelle connexion
- **Transférer le rôle GG** à n'importe quel joueur connecté (bloqué si le joueur est déjà GG)

> ⚠ **Sécurité de session** (v4.8.0) : à la réutilisation d'un profil hors-ligne (`on_join_player`), le flag `is_gg` est désormais explicitement remis à `False` avant traitement, empêchant un nouvel arrivant d'hériter du rôle d'un ancien GG sur le même personnage.

### Gestion des Questions

- **🃏 Reset Questions** — remet à zéro le pool de questions déjà posées sans toucher aux scores ni aux joueurs.
- **🔄 Relancer question** — relance la question en cours avec un timer frais et remet les réponses à zéro.
- **+10s** — ajoute 10 secondes au timer de la question en cours.

> Les boutons **+10s** et **Pause** émettent un toast d'erreur explicite si aucun quiz n'est actif (au lieu d'échouer silencieusement).

### Timer Mode Libre

- **-5 min / +5 min / +10 min** (`/api/admin/adjust-timer`) — décale `libre_ends_at` et émet `timer_update` ; répercuté en temps réel sur tous les mobiles et le projecteur. Un plancher de 10 s empêche de passer dans le négatif.
- **↺ Reset 15 min** (`/api/admin/reset-timer`) — réarme le chrono Mode Libre à 15 min à partir de maintenant.
- **🎯 Lancer Quiz** — lance un nouveau quiz directement depuis le Mode Libre (identique à l'action GG).

> `adjust-timer` n'opère que si un timer LIBRE est armé (`libre_ends_at` non nul) ; sinon l'action est ignorée. Depuis v4.7.0 le rollback arme ce timer automatiquement, donc les boutons fonctionnent dès l'entrée en LIBRE.

### Toasts d'erreur

Le Chuck Mode reçoit un toast d'erreur pour les actions refusées par le serveur (pause sans quiz actif, ajustement de timer sans timer actif, transfert GG vers un joueur déjà GG, etc.).

### Gestion des Scoops

- Liste temps réel des scoops entrants (auto-refresh sur `scoop_new`, `scoop_published`, `scoop_deleted`, `scoop_pinned`)
- Affichage de l'**image** uploadée si présente (`.scoop-image`)
- **Supprimer** un scoop (retiré du flux Projecteur)
- **Épingler** un scoop (re-affichage prioritaire en popup plein écran)
- **Poster en tant que Gossip Girl** (officiel, ✦ doré) — `POST /api/admin/post-as-gg`
- **Poster en tant que Chuck Bass** (voix off, non-officielle, sans ✦) — `POST /api/admin/post-as-chuck` *(v4.8.0)*

### ♠ Chuck Blast Composer *(v4.8.0)*

Onglet dédié `♠ Blast` dans la sidebar — équivalent admin du VIP Blast de Blair, en plus puissant :

- **Sélecteur d'identité** : Chuck Bass (rouge crimson) · Gossip Girl (or) · Custom (nom libre)
- **Textarea 500 caractères** avec compteur live + raccourci **Ctrl+↵**
- **Upload d'image** (réutilise `/api/upload-image`)
- **Dropdown son d'accompagnement** : New GG (défaut) · XOXO · Quiz Start · Correct · Countdown · Mode Libre · Aucun
- **Toggle 📌 Auto-pin** : épingle automatiquement le scoop dans les feeds
- **Templates one-tap** chargés depuis `blast-presets.json` (éditable sans redémarrage)
- **Aperçu live** à droite : rendu en miniature du projecteur, mis à jour à chaque frappe / changement d'identité

À l'envoi : `POST /api/admin/blast` persiste un `Scoop` (identité résolue serveur-autoritatif), émet `admin_blast` (overlay plein écran sur projector / mobile / vip, thématisé selon l'identité), `play_sound` optionnel, et `scoop_published` (refresh des Scoop Managers).

L'overlay `#admin-blast-overlay` est multi-thème :

| Identité | Thème | Icône | Eyebrow | Signature |
|---|---|---|---|---|
| `chuck` | crimson + or | ♠ | EMPIRE INSIDER | — Chuck Bass |
| `gg` | or | ✦ | GOSSIP GIRL DECREE | XOXO, Gossip Girl |
| `custom` | crème | ✧ | BREAKING NEWS | — *(nom personnalisé)* |

### Outils Projecteur

- Afficher / masquer le **QR Code** sur le grand écran
- Afficher les **Scores en grand** (overlay top 10)
- Déclencher les sons manuellement (Sound Board flottant)
- **Refresh projecteur** — rechargement de la page projecteur
- **Refresh mobiles** — rechargement forcé de tous les mobiles
- **Toggle Son ON/OFF** — mute global du projecteur (clé payload `muted`, sémantique unifiée)

### Log d'Activité

Toutes les actions admin sont tracées en DB (`ActivityLog`) avec timestamp, acteur et détail. Visible dans la section Logs du dashboard.

---

## 🔌 Logique de Connexion / Reconnexion

La reconnexion ne passe **pas** par un query-string : le token est rejoué via l'événement `reconnect_player` une fois la socket connectée. Le token persistant est stocké en `localStorage` sous la clé `gg_player_token` (utilisée par `mobile.html` ET `vip.html`).

```
Client                                          Serveur
  │                                                │
  ├─ io()  (connexion socket) ───────────────────►│ on_connect()  (envoie roster_state)
  │                                                │
  │  Au 'connect', si un token est stocké :        │
  ├─ reconnect_player { token } ─────────────────►│ on_reconnect()
  │  + get_game_state ────────────────────────────►│   └─ lookup player_token en DB
  │                                                │      ├─ trouvé  → update SID, is_connected=True
  │◄── reconnect_success { player, state, gg, ─────┤      │           → reconnect_success (état complet)
  │      ends_at, leaderboard }                    │      └─ inconnu → reconnect_failed
  │◄── reconnect_failed (sinon : purge token) ─────┤
  │                                                │
  │  Nouveau joueur (pas de token) :               │
  ├─ join_player { prénom, perso, blair_vip, ─────►│ on_join_player()
  │      blair_code }                              │   ├─ Blair + token VIP → blair_vip_granted (→ /vip)
  │◄── login_success { player, is_gg, phase… } ────┤   └─ sinon → login_success | login_error
```

Un personnage ne peut être sélectionné que si son slot n'est pas déjà occupé (`is_connected == False` en DB, vérifié par un index unique partiel `uq_personnage_connected`). Blair Waldorf requiert en plus le `BLAIR_SECRET_CODE` (ou le token VIP dans l'URL).

À la **déconnexion** (`on_disconnect`), le serveur passe `is_connected=False`, libère le SID (`session_id = None`, évite les *ghost emits*), purge le flag de session `blair_vip_verified` (depuis v4.8.0) et diffuse `player_left` + `leaderboard_update`.

### Rehydratation du feed scoop (v4.8.0)

Sur `socket.on('connect')`, mobile et VIP appellent `loadScoopHistory()` (fetch `/api/scoops?limit=30`) pour reconstruire le feed après un F5 ou une reconnexion réseau. Un set `_renderedScoopIds` évite les doublons quand un événement live et l'historique se croisent.

---

## 📡 Événements Socket.io — référence

> Source de vérité : `app/events.py` (handlers `@socketio.on`) et les `socketio.emit(...)` de `app/main.py` / `app/events.py`. « Tous » = broadcast ; « Émetteur » = renvoyé au seul client à l'origine de l'action.

### Serveur → Clients

**Phases & cycle de vie**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `phase_changed` | Tous | Changement d'état (`LOBBY` / `QUIZ` / `QUIZ_PAUSED` / `LIBRE`) |
| `game_paused` | Tous | Bascule de la pause globale (`is_paused`) |
| `quiz_paused` | Tous | Quiz mis en pause (`QUIZ_PAUSED`) |
| `quiz_ended` | Tous | Quiz terminé (pool épuisé) → nouveau GG |
| `quiz_stopped` | Tous | Arrêt manuel + rollback admin |
| `session_reset` | Tous | Reset complet (`/reset`) **ou** reset scores (`/reset-scores`, payload `keep_players:true`) |
| `game_state` | Émetteur | Réponse à `get_game_state` |

**Quiz & questions**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `new_question` | Tous | Début d'un round |
| `question_tick` | Tous | Décompte de la question (chaque seconde) |
| `question_answer` | Tous | Révélation (fin du timer ou All-In) |
| `answer_confirmed` | Émetteur | Réponse enregistrée (feedback individuel) |
| `answer_progress` | Tous | Compteur de réponses + dernier répondant |
| `answer_error` | Émetteur | Réponse refusée (doublon, GG, hors délai) |
| `gg_pick_question` | GG | Candidates prêtes pour le pick |
| `waiting_for_gg_pick` | Tous | GG en train de choisir |
| `gg_quiz_ready` | GG | Invitation à lancer le quiz (depuis LIBRE) |
| `gg_updated_silent` | Tous | Mise à jour silencieuse du rôle GG |
| `new_gg` | Tous | Nouveau Gossip Girl désigné |
| `leaderboard_update` | Tous | Scores modifiés |

**Timer Mode Libre**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `timer_tick` | Tous | Décompte LIBRE piloté par le serveur (chaque seconde) |
| `timer_update` | Tous | Nouvelle date de fin LIBRE (`adjust-timer` / `reset-timer`) |

**Présence & session**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `roster_state` | Émetteur | Snapshot des personnages occupés (au connect ou sur `get_roster`) |
| `reconnect_success` | Émetteur | Reconnexion par token réussie (état complet) |
| `reconnect_failed` | Émetteur | Token inconnu → le client purge son token |
| `login_success` / `login_error` | Émetteur | Résultat de `join_player` |
| `blair_vip_granted` | Émetteur | Blair authentifiée → redirection `/vip` |
| `player_joined` / `player_left` | Tous | Arrivée / départ d'un joueur |
| `pong_server` | Émetteur | Réponse heartbeat à `ping_client` |
| `force_logout` | Joueur ciblé | Expulsion (kick) |
| `client_reload` | Ciblé ou tous | Rechargement forcé (un mobile ou tous) |

**Scoops & Blasts**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `new_scoop` | Tous | Nouveau scoop publié (popup projecteur + ajout au feed) |
| `scoop_submitted` | Émetteur | Confirmation au publieur |
| `scoop_ticker` | Tous | Scoop en bandeau pendant le quiz |
| `scoop_published` | Tous | Refresh des Scoop Managers (Blast VIP / Chuck Blast) |
| `admin_new_scoop` | Admin | Notification Chuck Mode |
| `scoop_pinned` | Tous | Scoop épinglé (popup projecteur) |
| `scoop_deleted` | Tous | Scoop retiré du flux |
| `vip_blast` | Tous | Overlay VIP Blast plein écran (mobile / VIP / projecteur — overlay dédié) |
| `admin_blast` | Tous | Overlay Chuck Blast plein écran multi-thème (chuck / gg / custom) |

**Audio & Projecteur**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `play_sound` / `stop_sound` | Projecteur | Jouer / stopper un son |
| `sound_started` / `sound_stopped` | Tous | État du son (relais depuis le projecteur) |
| `projector_sound_state` | Admin | État mute du projecteur |
| `projector_toggle_sound` | Projecteur | Mute / unmute (payload `{muted: bool}`) |
| `projector_qr` | Projecteur | Afficher / masquer le QR Code |
| `projector_scores` | Projecteur | Overlay scores top 10 |
| `projector_reload` | Projecteur | Rechargement de la page projecteur |
| `projector_message` | Projecteur | Message custom plein écran |

**Admin & divers**

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `admin_error` | Admin | Action refusée (toast Chuck Mode) |
| `turbo_mode_changed` | Tous | Bascule du mode turbo (tests bots) |
| `bot_log` | Admin | Stream temps réel des logs de bots |

### Clients → Serveur

| Événement | Émetteur | Rôle |
|---|---|---|
| `reconnect_player` | Joueur | Rejouer le token persistant pour se reconnecter |
| `get_game_state` | Joueur | Demander l'état courant (au connect) |
| `get_roster` | Joueur | Demander la liste des personnages occupés |
| `join_player` | Joueur | Login (prénom, personnage, Blair VIP / code) |
| `ping_client` | Joueur | Heartbeat (→ `pong_server`) |
| `admin_identify` | Admin | S'identifier (ciblage des logs bots) |
| `projector_reconnect` | Projecteur | Resynchronisation après F5 |
| `gg_start_quiz` | GG / Admin | Lancer le quiz |
| `gg_ready_to_pick` | GG | Confirmer prêt à choisir |
| `gg_choose_question` | GG | Choisir une candidate |
| `submit_answer` | Joueur | Soumettre une réponse |
| `post_scoop` | Joueur / GG | Publier un scoop |
| `sound_started` / `sound_stopped` | Projecteur | Confirmer le début / la fin d'un son (source de vérité audio) |
| `projector_sound_state` | Projecteur | Remonter l'état mute du projecteur |

---

## 🛠 Routes API Admin (Chuck Mode)

Toutes les routes `/api/admin/*` et `/admin/bots/*` sont protégées par `@admin_required` (session Flask).

**Phases & quiz**

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/phase` | Change de phase : `QUIZ` / `LIBRE` / `LOBBY` / `PAUSE` / `PAUSE_QUIZ` / `RESUME_QUIZ` / `STOP_QUIZ` |
| `POST` | `/api/admin/reload-question` | Relance la question en cours avec un timer frais |
| `POST` | `/api/admin/force-gg-quiz` | Coupe le Mode Libre → invite le GG à lancer un quiz |
| `POST` | `/api/admin/adjust-timer` | Ajuste le timer actif (+10s en QUIZ ; -5/+5/+10 min en LIBRE) |
| `POST` | `/api/admin/reset-timer` | Réarme le timer Mode Libre à 15 min |

**Resets**

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/reset` | Reset complet (joueurs + scores + scoops) → LOBBY |
| `POST` | `/api/admin/reset-scores` | Remet les scores + scoops à zéro, **conserve** les joueurs |
| `POST` | `/api/admin/reset-questions` | Remet à zéro le pool de questions posées |

**Joueurs**

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/transfer-gg` | Transfère le rôle GG à un joueur |
| `POST` | `/api/admin/kick-player` | Expulse un joueur (disconnect forcé + `force_logout`) |
| `POST` | `/api/admin/delete-player` | Supprime un joueur (cascade scores) |
| `POST` | `/api/admin/free-blair` | Libère le slot Blair (réinitialise `is_connected`) |
| `POST` | `/api/admin/refresh-clients` | Recharge tous les mobiles |
| `POST` | `/api/admin/refresh-player` | Rechargement ciblé d'un mobile |
| `POST` | `/api/admin/adjust-score` | Ajuste manuellement le score d'un joueur |

**Scoops & Blast**

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/delete-scoop` | Supprime un scoop |
| `POST` | `/api/admin/pin-scoop` | Épingle un scoop |
| `POST` | `/api/admin/post-as-gg` | Publie un scoop officiel Gossip Girl (✦ doré) |
| `POST` | `/api/admin/post-as-chuck` | Publie un scoop signé Chuck Bass (voix off, non-officiel) |
| `POST` | `/api/admin/blast` | **Chuck Blast Composer** — overlay plein écran multi-thème + scoop persisté + son optionnel + auto-pin |
| `GET`  | `/api/admin/blast-presets` | Templates one-tap du composer (lit `blast-presets.json` à chaque appel) |
| `POST` | `/api/admin/custom-message` | Message custom plein écran sur le projecteur |

**Sons & projecteur**

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/play-sound` | Déclenche un son sur le projecteur |
| `POST` | `/api/admin/stop-sound` | Stoppe le son en cours |
| `POST` | `/api/admin/projector-qr` | Affiche / masque le QR Code |
| `POST` | `/api/admin/projector-scores` | Affiche / masque l'overlay scores |
| `POST` | `/api/admin/projector-refresh` | Recharge la page projecteur |
| `POST` | `/api/admin/projector-sound` | Mute / unmute le projecteur (payload `{muted: bool}`) |

**Données (GET) & outils**

| Méthode | Route | Description |
|---|---|---|
| `GET`  | `/api/admin/players` | Liste des joueurs |
| `GET`  | `/api/admin/leaderboard` | Leaderboard complet |
| `GET`  | `/api/admin/scores-history` | Historique des scores par quiz |
| `GET`  | `/api/admin/scoops` | Liste des scoops |
| `GET`  | `/api/admin/logs` | Journal d'activité admin |
| `GET`  | `/api/admin/game-state` | État courant du jeu |
| `POST` | `/api/admin/turbo-mode` | Bascule le mode turbo (tests bots) |
| `POST` | `/admin/bots/deploy` · `GET /admin/bots` · `DELETE /admin/bots/<id>` · `POST /admin/bots/remove_all` · `GET /admin/bots/logs` | Orchestration des bots de test |

### Routes publiques (sans authentification)

| Méthode | Route | Description |
|---|---|---|
| `GET`  | `/` → `/mobile` · `/projector` · `/qr` | Pages invité / projecteur / QR |
| `GET`  | `/health` | Healthcheck (`{"status":"ok"}`) |
| `GET`  | `/api/characters` | Roster Gossip Girl (lit `characters.json`) |
| `GET`  | `/api/scoops` | Scoops publics (flux mobile / projecteur) |
| `GET`  | `/api/qr-code` | QR Code base64 (overlay projecteur) |
| `POST` | `/api/upload-image` | Upload d'une photo de scoop (retourne `image_path = /uploads/<fname>`) |
| `GET`  | `/uploads/<filename>` | Sert les images uploadées (`send_from_directory(UPLOAD_FOLDER)`) |
| `GET`  | `/static/uploads/<filename>` | **Alias rétro-compat** vers `/uploads/` pour les scoops persistés avant v4.8.0 |

## 💎 Routes API VIP (Blair Waldorf)

Toutes les routes sont protégées par `@vip_required` (session Flask — `blair_vip_verified`).

| Méthode | Route | Description |
|---|---|---|
| `GET`  | `/vip` | Interface VIP Blair (template `vip.html`) |
| `GET`  | `/blair-vip` | Auth token VIP → pose session + redirige vers `/vip` |
| `GET`  | `/api/vip/gallery` | Liste des photos Dino Gallery (JSON) |
| `GET`  | `/api/vip/sounds` | Liste des sons du Soundboard avec URLs MP3 (JSON) |
| `GET`  | `/api/vip/scoops` | Liste des scoops publiés (JSON, image_path inclus) |
| `POST` | `/api/vip/scoops/<id>/pin` | Épingle un scoop (émet `scoop_pinned`) |
| `POST` | `/api/vip/scoops/<id>/delete` | Supprime un scoop (émet `scoop_deleted`) |
| `POST` | `/api/vip/blast` | Publie un scoop attribué à **Blair Waldorf** + émet `vip_blast` (overlay plein écran sur mobile / VIP / projecteur) + `play_sound(new_gg)` + `scoop_published` |

---

*XOXO, Gossip Girl — You know you love me.*
