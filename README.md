# 🖤 XOXO — Gossip Girl Party App `v4.6.2`

> *The one and only source into the scandalous lives of Manhattan's elite.*

Application événementielle temps réel pour soirée à thème Gossip Girl.  
Stack : **Flask · Flask-SocketIO · APScheduler · SQLite · Docker**

> **Version actuelle : v4.6.2** — 2026-05-19

---

## 🏗 Architecture

L'application repose sur une **machine à états stricte** côté serveur :

```
LOBBY ──► QUIZ ──► LIBRE ──► QUIZ ──► ...
               └──► QUIZ_PAUSED (via Chuck Mode)
```

- Le serveur est le **Master Clock** : les timers sont gérés par APScheduler (threads background). Toute émission Socket.io depuis ces threads est encapsulée dans `with app.app_context()`.
- **Zéro rechargement de page** : l'UI mute entièrement via événements Socket.io — changement de phase, questions, scores, scoops.
- Chaque joueur possède un **`player_token` persistent** (localStorage) pour la reconnexion transparente en cas de coupure réseau.
- **`game.question_index`** = numéro de round courant (0-based). `game.get_current_question()` retourne la question sérialisée en DB, indépendamment de toute liste en mémoire.
- **Questions à la demande** (v4.5) : plus de pré-sélection globale en début de quiz. Les candidates sont tirées round par round via `pick_one_per_category()` — 1 question par catégorie non épuisée.

---

## 📁 Structure du Projet

```
gossip-girl-party/
│
├── app/                        # Code Python (package)
│   ├── __init__.py             # Initialisation du package
│   ├── __main__.py             # Point d'entrée : python -m app
│   ├── main.py                 # ★ Flask app factory + routes HTTP + logique métier
│   │                           #   (machine à états, timers APScheduler, routes admin, routes VIP)
│   ├── events.py               # ★ Tous les handlers Socket.io (connect, quiz, scoops…)
│   └── models.py               # ★ SQLAlchemy : Player, GameSession, Score, Scoop,
│                               #   ActivityLog, ReservedSlot + helpers DB
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
│   └── gossipgirl_qcm.jsonl    # Base de questions (format JSON Lines — 259 questions, 8 catégories)
│
├── data/                       # ⚠ Créé automatiquement — persisté par Docker Volumes
│   ├── db/                     # gossip.db (SQLite)
│   └── uploads/                # Photos uploadées par les invités
│
├── .env                        # ⚠ Ne jamais committer — copié depuis .env.example
├── .env.example                # Template de configuration (toutes les variables)
├── Dockerfile                  # Image Python 3.11-slim + dépendances système (Pillow)
├── docker-compose.yml          # Orchestration : ports, volumes nommés, healthcheck
├── Makefile                    # Raccourcis : make up / make down / make logs / make shell
└── requirements.txt            # Dépendances Python (Flask, SocketIO, APScheduler…)
```

> **Fichiers à ignorer / à ne pas committer** (déjà dans `.gitignore`) :  
> `app/__pycache__/`, `*.pyc`, `data/`, `.env`, `static/uploads/*`

---

## 🎮 Déroulement d'une soirée

### 1. Lobby

Les invités se connectent sur `/mobile`, choisissent leur prénom et leur personnage Gossip Girl. Blair Waldorf est un slot VIP nécessitant le `BLAIR_SECRET_CODE` (ou le lien QR VIP). Le Gossip Girl (GG) est le maître du jeu — son rôle est assigné manuellement via le Chuck Mode ou transféré en fin de quiz.

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
| `category` | string | Catégorie (affiché dans le pick panel GG) |
| `question` | string | Énoncé |
| `choices` | dict `{A,B,C,D}` | Quatre choix de réponse |
| `answer` | string `A/B/C/D` | Lettre de la bonne réponse |
| `explanation` | string | Explication affichée après la révélation |

### Catégories disponibles (259 questions)

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
| `SECRET_KEY` | Clé de session Flask (changer absolument) | *(obligatoire)* |
| `ADMIN_PASSWORD` | Mot de passe Chuck Mode | `ChuckBassGodMode2026` |
| `BLAIR_SECRET_CODE` | Code secret pour jouer Blair Waldorf | `24042024` |
| `BLAIR_VIP_TOKEN` | Token QR VIP Blair (lien direct sans code, ouvre aussi `/vip`) | *(long token unique)* |
| `APP_HOST` | Hôte d'écoute Flask | `0.0.0.0` |
| `APP_PORT` | Port unique Flask + Docker (interne = externe) | `7777` |
| `DEBUG` | Mode debug Flask | `false` |
| `QUESTIONS_PER_SESSION` | Nombre de rounds par quiz | `10` |
| `PHASE2_DURATION_MINUTES` | Durée du Mode Libre en minutes | `15` |
| `POST_DELAY_SECONDS` | Délai avant publication d'un scoop | `3` |
| `DB_PATH` | Chemin relatif vers la base SQLite | `data/db/gossip.db` |
| `UPLOAD_PATH` | Dossier de stockage des photos | `data/uploads` |
| `QUESTIONS_PATH` | Chemin vers le fichier de questions | `questions/gossipgirl_qcm.jsonl` |

---

## 💎 VIP Mode — Blair Waldorf

### Accès

Le VIP Mode est exclusif à Blair Waldorf. L'accès se fait via le **lien QR VIP** :

```
/blair-vip?token=<BLAIR_VIP_TOKEN>
```

Ce lien :
1. Vérifie le token
2. Pose `session["blair_vip_verified"] = True`
3. Redirige vers `/vip`

Toutes les routes `/vip` et `/api/vip/*` sont protégées par le décorateur `@vip_required` — sans la session vérifiée, la requête est redirigée vers `/mobile`.

### Dashboard VIP — 4 tuiles

| Tuile | Panneau | Description |
|---|---|---|
| 🐱 Dino Gallery | `vip-panel-gallery` | Carrousel photo avec gestes swipe, indicateurs de points, transition fondu |
| 🔊 VIP Soundboard | `vip-panel-soundboard` | 6 boutons audio ; MP3 si disponibles, sinon tonalités Web Audio API |
| 📰 Scoops Manager | `vip-panel-scoops-manager` | Liste des scoops avec actions Épingler / Supprimer + toggle QR Code |
| ✨ Custom Blast | `vip-panel-blast` | Poster un scoop officiel Gossip Girl avec animation wow sur tous les mobiles |

### Ajouter des médias (drop-in, sans modifier le code)

| Dossier | Contenu | Effet |
|---|---|---|
| `static/dino/` | `*.jpg` / `*.png` / `*.webp` | Remplace les emojis fallback dans la galerie |
| `static/sounds/vip/` | `champagne.mp3` · `drama.mp3` · `gossip.mp3` · `scandale.mp3` · `suspens.mp3` · `victoire.mp3` | Active la lecture MP3 sur le soundboard (fallback Web Audio API si absent) |

### Événements Socket.io VIP

| Événement | Direction | Description |
|---|---|---|
| `vip_blast` | Serveur → Tous | Déclenche l'overlay Blast animé sur tous les mobiles |
| `scoop_pinned` | Serveur → Tous | Scoop épinglé re-affiché en popup sur le projecteur |
| `scoop_deleted` | Serveur → Tous | Scoop retiré du flux |

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
| `new_gg.mp3` | 6.06s | Nouveau Gossip Girl désigné / Blast VIP |
| `phase2_start.mp3` | 14.9s | Début de phase LIBRE |
| `timer_end.mp3` | 21.3s | Fin du timer LIBRE |
| `countdown.mp3` | 2.3s | Manuel uniquement (Chuck Mode) |

> La durée de `new_gg.mp3` est utilisée comme constante `NEW_GG_AUDIO_DURATION = 6.06` dans `main.py` pour synchroniser le démarrage du Mode Libre avec la fin de l'animation. Ce même son est déclenché lors d'un **Blast VIP** pour l'effet wow-factor.

### Architecture audio — flux complet

```
Chuck Mode clique un bouton son
  → POST /api/admin/play-sound  {sound: "xoxo"}
  → socketio.emit("play_sound", {sound})
  → Projecteur reçoit play_sound → playSound("xoxo")
  → Projecteur joue le son → socket.emit("sound_started", {sound, duration})
  → Serveur relaie sound_started à tous les clients
  → Chuck Mode reçoit sound_started → barre de progression + bouton Stop actif

Son automatique (APScheduler, transition de phase...)
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

### Unlock Audio — Overlay Bienvenue

Un overlay plein écran s'affiche au chargement de `/projector`. Au premier clic, l'`AudioContext` est créé et résumé, l'overlay disparaît en fondu. **Le projecteur doit toujours être ouvert et l'overlay cliqué avant de lancer une session.** Sans ce clic initial, tous les `audio.play()` seront rejetés silencieusement par le navigateur.

---

## 👑 Chuck Mode — Interface Admin

Accessible via `/xoxo-admin` (mot de passe défini dans `.env` : `ADMIN_PASSWORD`).

### Contrôle des Phases

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

Le **Rollback** (`STOP_QUIZ`) arrête le quiz en cours et permet de revenir en LOBBY sans corrompre les scores enregistrés.

### Gestion des Joueurs

- Vue temps réel des joueurs connectés (UUID + Socket ID + personnage + score)
- **Expulser** un joueur (disconnect forcé + invalidation du token localStorage)
- **Supprimer** un joueur (irréversible — supprime Player + Scores en cascade)
- **↻ Refresh ciblé** — recharge le mobile d'un joueur spécifique sans affecter les autres
- **Libérer le slot Blair** — réinitialise `is_connected` pour permettre une nouvelle connexion
- **Transférer le rôle GG** à n'importe quel joueur connecté (bloqué si le joueur est déjà GG)

### Gestion des Questions

- **🃏 Reset Questions** — remet à zéro le pool de questions déjà posées sans toucher aux scores ni aux joueurs. Utile pour rejouer le même quiz avec un pool frais.
- **🔄 Relancer question** — relance la question en cours avec un timer frais et remet les réponses à zéro.
- **+10s** — ajoute 10 secondes au timer de la question en cours.

> Les boutons **+10s** et **Pause** émettent désormais un toast d'erreur explicite si aucun quiz n'est actif (au lieu d'échouer silencieusement).

### Timer Mode Libre

- **+5 min / +10 min** — ajoute du temps au chrono Mode Libre, répercuté en temps réel sur tous les mobiles et le projecteur.
- **🎯 Lancer Quiz** — lance un nouveau quiz directement depuis le Mode Libre (identique à l'action GG, sans bug de séquence).

### Toasts d'erreur

Le Chuck Mode reçoit désormais un toast d'erreur pour les actions refusées par le serveur (pause sans quiz actif, ajustement de timer sans timer actif, transfert GG vers un joueur déjà GG, etc.).

### Gestion des Scoops

- Liste en temps réel des scoops entrants
- **Supprimer** un scoop (retiré du flux Projecteur)
- **Épingler** un scoop (re-affichage prioritaire en popup plein écran)
- **Poster en tant que Gossip Girl** (scoop officiel)

### Outils Projecteur

- Afficher / masquer le **QR Code** sur le grand écran
- Afficher les **Scores en grand** (overlay top 10)
- Déclencher les sons manuellement
- **Refresh projecteur** — rechargement de la page projecteur
- **Refresh mobiles** — rechargement forcé de tous les mobiles

### Log d'Activité

Toutes les actions admin sont tracées en DB (`ActivityLog`) avec timestamp, acteur et détail. Visible dans la section Logs du dashboard.

---

## 🔌 Logique de Connexion / Reconnexion

```
Client                                    Serveur
  │                                          │
  ├─ localStorage.getItem('player_token') ──►│
  │  (génère token UUID si absent)           │
  │                                          │
  ├─ io({ query: { player_token } }) ───────►│ on_connect()
  │                                          │   └─ lookup player_token en DB
  │                                          │      ├─ trouvé  → update SID → reconnect_success
  │◄── reconnect_success ────────────────────┤      └─ inconnu → attente join_player
  │    (payload état complet)                │
  │                                          │
  ├─ join_player (prénom, perso) ───────────►│ (si nouveau joueur)
  │◄── login_success ────────────────────────┤
```

Un personnage ne peut être sélectionné que si son slot n'est pas déjà occupé (`is_connected == False` en DB). Blair Waldorf requiert en plus le `BLAIR_SECRET_CODE` (ou le token VIP dans l'URL).

---

## 📡 Événements Socket.io — référence rapide

### Serveur → Clients

| Événement | Destinataire | Déclencheur |
|---|---|---|
| `phase_changed` | Tous | Changement d'état (LOBBY/QUIZ/LIBRE) |
| `new_question` | Tous | Début d'un round de quiz |
| `question_tick` | Tous | Chaque seconde pendant le timer |
| `question_answer` | Tous | Fin du timer ou All-In |
| `answer_confirmed` | Joueur individuel | Réponse reçue par le serveur |
| `answer_progress` | Tous | Mise à jour du compteur de réponses |
| `leaderboard_update` | Tous | Score modifié |
| `quiz_paused` | Tous | Pause déclenchée |
| `quiz_ended` | Tous | Fin du quiz |
| `gg_pick_question` | GG | Candidates prêtes pour le pick |
| `waiting_for_gg_pick` | Tous | GG en train de choisir |
| `scoop_published` | Tous | Nouveau scoop validé |
| `admin_error` | Admin | Action refusée (toast côté Chuck Mode) |
| `answer_error` | Joueur | Réponse refusée (doublon, hors délai…) |
| `timer_update` | Tous | Modification du timer Mode Libre |
| `client_reload` | Joueur ciblé | Rechargement ciblé d'un mobile spécifique |
| `vip_blast` | Tous | Overlay Blast animé déclenché par Blair VIP |
| `scoop_pinned` | Tous | Scoop épinglé re-affiché sur le projecteur |
| `scoop_deleted` | Tous | Scoop supprimé du flux |

### Clients → Serveur

| Événement | Émetteur | Rôle |
|---|---|---|
| `gg_start_quiz` | GG / Admin | Lancer le quiz |
| `gg_ready_to_pick` | GG | Confirmer prêt à choisir |
| `gg_choose_question` | GG | Choisir une candidate |
| `submit_answer` | Joueur | Soumettre une réponse |
| `post_scoop` | Joueur / GG | Publier un scoop |
| `projector_reconnect` | Projecteur | Resynchronisation après F5 |

---

## 🛠 Routes API Admin (Chuck Mode)

Toutes les routes sont protégées par `@admin_required` (session Flask).

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/admin/play-sound` | Déclenche un son sur le projecteur |
| `POST` | `/api/admin/stop-sound` | Stoppe le son en cours |
| `POST` | `/api/admin/reset-questions` | Remet à zéro le pool de questions posées (v4.5) |
| `POST` | `/api/admin/refresh-player` | Rechargement ciblé d'un mobile joueur (v4.5) |
| `POST` | `/api/admin/force-gg-quiz` | Force le GG à lancer un quiz depuis le Mode Libre (v4.5) |
| `POST` | `/api/admin/reload-question` | Relance la question en cours avec un timer frais |
| `POST` | `/api/admin/adjust-timer` | Ajoute du temps (+10s quiz, +5/+10 min Libre) |
| `POST` | `/api/admin/transfer-gg` | Transfère le rôle GG à un joueur |
| `POST` | `/api/admin/kick-player` | Expulse un joueur (disconnect forcé) |
| `POST` | `/api/admin/delete-player` | Supprime un joueur (cascade scores) |
| `POST` | `/api/admin/reset-scores` | Remet les scores à zéro |
| `POST` | `/api/admin/reset-all` | Reset complet (scores + joueurs + scoops) |
| `GET`  | `/api/admin/players` | Liste des joueurs connectés |
| `GET`  | `/api/admin/scores` | Leaderboard complet |
| `GET`  | `/api/admin/scoops` | Liste des scoops |
| `GET`  | `/api/admin/logs` | Journal d'activité admin |
| `GET`  | `/api/qr-code` | QR Code base64 (pour overlay projecteur) |

## 💎 Routes API VIP (Blair Waldorf)

Toutes les routes sont protégées par `@vip_required` (session Flask — `blair_vip_verified`).

| Méthode | Route | Description |
|---|---|---|
| `GET`  | `/vip` | Interface VIP Blair (template `vip.html`) |
| `GET`  | `/blair-vip` | Auth token VIP → pose session + redirige vers `/vip` |
| `GET`  | `/api/vip/gallery` | Liste des photos Dino Gallery (JSON) |
| `GET`  | `/api/vip/sounds` | Liste des sons du Soundboard avec URLs MP3 (JSON) |
| `GET`  | `/api/vip/scoops` | Liste des scoops publiés (JSON) |
| `POST` | `/api/vip/scoops/<id>/pin` | Épingle un scoop (émet `scoop_pinned`) |
| `POST` | `/api/vip/scoops/<id>/delete` | Supprime un scoop (émet `scoop_deleted`) |
| `POST` | `/api/vip/blast` | Publie un scoop officiel GG + émet `vip_blast` sur tous les mobiles |

---

*XOXO, Gossip Girl — You know you love me.*
