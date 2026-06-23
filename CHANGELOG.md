# Changelog — XOXO : The Gossip Girl App

Toutes les modifications notables sont documentées ici.  
Format : [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/)  
XOXO, Chuck Bass.

---

## [v4.8.0] — 2026-06-23

Durcissement issu de l'audit `AUDIT.md` (Bug A : routage upload — Bug B : audio Chuck Mode) + nouvelle **Chuck Blast Composer** (parité admin avec le VIP Blast de Blair) + refonte UX du top bar Chuck Mode pour usage iPhone Safari. Rehydratation du feed scoop à la reconnexion sur mobile et VIP.

### Added

#### Chuck Blast Composer (admin)

- **Section `♠ Blast`** (`templates/admin.html`) — nouvel item de sidebar dédié, panneau composer + aperçu live côte à côte. Sélecteur d'identité **Chuck Bass / Gossip Girl / Custom**, textarea 500 caractères avec compteur, upload d'image (réutilise `/api/upload-image`), dropdown son d'accompagnement (7 presets), toggle **📌 Auto-pin**, row de templates one-tap, raccourci **Ctrl+↵**.
- **Route `POST /api/admin/blast`** (`app/main.py`) — persiste un `Scoop` (avec `is_pinned` optionnel), émet `admin_blast` (payload : `content`, `image_path`, `author_name`, `is_official`, `identity`) + `play_sound` (si choisi, whitelist serveur) + `scoop_published` pour refresh des Scoop Managers. Identité résolue serveur-autoritatif (`gg`/`chuck`/`custom`).
- **Route `GET /api/admin/blast-presets`** — lit `blast-presets.json` à la volée (no restart).
- **Fichier `blast-presets.json`** à la racine — 5 templates par défaut (Quiz dans 5 min · Last call · Trouvez Dan · Toast minuit · Best dressed). Path configurable via `BLAST_PRESETS_PATH`.
- **Overlay `#admin-blast-overlay`** multi-thème (`theme-gg` or · `theme-chuck` crimson · `theme-custom` cream) sur **projector + mobile + vip**, déclenché par `socket.on('admin_blast', …)`. Icône, eyebrow et signature s'adaptent à l'identité.

#### Voix off Chuck

- **Panneau "Poster en tant que Chuck"** (`templates/admin.html`, section Scoops) — sous le panneau GG, publication attribuée nominativement à Chuck Bass (`is_official=False`, pas de tag ✦ doré).
- **Route `POST /api/admin/post-as-chuck`** (`app/main.py`).

#### Rehydratation du feed scoop

- **`loadScoopHistory()`** (`templates/mobile.html`, `templates/vip.html`) — appelée sur `socket.on('connect')` pour reconstruire le feed après F5 / reconnexion. Set `_renderedScoopIds` pour dedup (race live ↔ historique).

#### Top bar Chuck Mode (mobile iPhone Safari)

- **`.cmd-anchor`** (`templates/admin.html`) — cluster gauche figé : `☰` + brand + phase pill. Jamais scrollé, toujours visible.
- **`.cmd-scroller`** — strip droit avec `overflow-x: auto`, momentum iOS, scrollbar masquée, fade-mask CSS sur le bord droit comme indice de swipe.
- **Popover `#phase-menu`** — le phase pill devient `<button>` avec chevron `▾` ; tap → menu de 3 items (Lobby / Libre / Pause) ancré sous le pill, phase active en or. Ferme au clic dehors / ESC.

### Fixed

#### Routage upload — Bug A

- **Route `/uploads/<path:filename>`** (`app/main.py`) servie via `send_from_directory(UPLOAD_FOLDER)` — la route manquait totalement (l'import `send_from_directory` n'était pas utilisé), toutes les images de scoops retournaient 404 sur projector / mobile / vip. **`/static/uploads/<path>`** ajouté en alias rétro-compat pour les scoops persistés avec l'ancienne URL.
- **`api_upload_image`** retourne désormais `/uploads/<fname>` au lieu de `/static/uploads/<fname>`.

#### Audio Chuck Mode — Bug B

- **`toggleProjectorSound()`** (`templates/admin.html`) — payload `{ muted: !S.soundOn }` au lieu de `{ enabled: S.soundOn }`. Le serveur lit `data.get("muted")`, le toggle ne faisait donc strictement rien : `muted` valait toujours `False` (clé absente → défaut), le projecteur recevait `muted: false` à chaque clic, et la pill admin basculait uniquement son texte local.
- **`triggerSound()`** (`templates/admin.html`) — URL `'\api\admin\play-sound'` (backslashes interprétés comme escapes JS → `apiadminplay-sound`) corrigée en `'/api/admin/play-sound'`. Toute la sound board admin retournait 404 silencieusement.

#### Sessions & sécurité

- **Reset `is_gg = False`** sur réutilisation d'un profil offline (`app/events.py`, `on_join_player`) — un nouveau joueur qui se connectait sur le slot d'un ancien GG héritait du flag `is_gg=True`, contournant la branche d'attribution Dan Humphrey. **[Defect S1]**
- **`session.pop("blair_vip_verified", None)`** sur `on_disconnect` (`app/events.py`) — le flag VIP persistait indéfiniment dans le cookie Flask, n'étant nettoyé que par `admin_logout`. Un téléphone qui changeait de joueur conservait l'accès `/vip` jusqu'à expiration cookie. **[Defect S2]**

#### Rendu & ordre des scoops

- **`postAsGG()`** (`templates/admin.html`) — payload `{ content: … }` au lieu de `{ message: … }`. Le serveur lit `data.get("content")`, donc le bouton "Publier en tant que GG" retournait toujours **"✗ Contenu vide."**.
- **Labels auteur** (`templates/projector.html` × 2 sites, `mobile.html`, `vip.html` × 2 sites, `admin.html`) — `is_official ? '✦ Gossip Girl' : author_name` remplacé par `(is_official ? '✦ ' : '') + author_name`. Les blasts de Blair s'affichaient encore comme "✦ Gossip Girl" sur tous les feeds malgré l'attribution DB correcte.
- **Feed Scoop Manager admin** (`templates/admin.html`) — `s.author / s.message / s.time / s.pinned / s.is_gg` (clés inexistantes côté `Scoop.to_dict()`) alignées sur les vraies clés `author_name / content / created_at / is_pinned / is_official`. Toutes les entrées s'affichaient "Anonyme" avec texte vide depuis la mise en place du panneau.
- **Ordre du feed projecteur** (`templates/projector.html`, `loadScoopHistory`) — `prepend=true` au lieu de `append + scrollTop=scrollHeight` ; le plus récent finit désormais en tête de liste, conforme à l'intention documentée du commentaire.

#### Affichage des images dans les Scoop Managers

- **`.sm-image`** (`templates/vip.html`) — règle CSS + balise `<img class="sm-image" src="${s.image_path}">` dans `_smRender`. Les scoops avec image apparaissent maintenant dans le Scoop Manager de Blair.
- **`.scoop-image`** (`templates/admin.html`) — même fix côté Chuck Mode, le HTML de `loadScoops` rend désormais l'image.

#### VIP Blast — branding et doublon projecteur

- **`api_vip_blast`** (`app/main.py`) — `author_name = "Blair Waldorf"` (au lieu de "Gossip Girl") + suppression de l'emit `new_scoop` qui doublait l'overlay VIP avec le popup projecteur générique "✦ Gossip Girl". Emit `scoop_published` ajouté pour rafraîchir les Scoop Managers.
- **Overlay VIP Blast projecteur** (`templates/projector.html`) — `#vip-blast-overlay` dédié (💎 + "New VIP Blast" + signature `✦ Blair Waldorf ✦`) déclenché par `socket.on('vip_blast', …)`. Plus de popup générique pour les blasts.

### Changed

- **Scoop Manager admin** auto-refresh sur `scoop_published`, `scoop_deleted`, `scoop_pinned` (`scoop_new` conservé en back-compat).
- **Payload `postAsGG` / `postAsChuck`** — clé harmonisée à `content` (cohérent avec `api_vip_blast` et `api_admin_blast`).
- **`updatePhaseUI`** (`templates/admin.html`) écrit désormais dans `#phase-pill-label` au lieu de remplacer `pill.innerHTML` (préserve dot + chevron) ; marque l'élément actif dans `#phase-menu`.

### Removed

- **`_enter_lobby(game, *, emit_event)`** (`app/main.py`) — helper de retour LOBBY jamais appelé (toutes les transitions passent par `game.transition_to(STATE_LOBBY)` directement). Code mort.
- **`ReservedSlot.held_by`** (`app/models.py`) — colonne jamais lue ni écrite ailleurs. Slot Blair n'utilise que `personnage` + `is_locked`.
- **Inline `.cmd-phase-group`** + **`.cmd-spacer`** + **`.cmd-right`** (`templates/admin.html`) — remplacés par le popover phase-menu + la structure `.cmd-anchor` / `.cmd-scroller`.

---

## [v4.7.0] — 2026-05-20

Durcissement issu de l'audit de code v4.7, complété par deux correctifs architecturaux validés en QA (protocole `AXE 1→5`) sur la **gestion de session** et les **timers de la machine à états**. Aucune nouvelle fonctionnalité.

### Fixed

#### Session & reconnexion

- **`reset-scores` ne déconnecte plus les joueurs** (`app/main.py`, `templates/mobile.html`, `templates/vip.html`) — `/api/admin/reset` (wipe complet) et `/api/admin/reset-scores` (reset partiel) émettaient le même `session_reset`, si bien que les clients purgeaient systématiquement leur token de reconnexion et retombaient sur l'écran de login. Sur un reset des scores les profils sont conservés en DB : les joueurs doivent rester connectés. Seule Blair survivait, par accident, via son fallback `blair_vip`. **[AXE 1]**
- **Clé `localStorage` du kick** (`templates/mobile.html`) — les handlers `force_logout` / `session_reset` ciblaient `player_token` au lieu de `gg_player_token`, permettant une ré-authentification silencieuse après une expulsion. Clé corrigée.
- **`Player.session_id` `nullable=False` → `nullable=True`** (`app/models.py`) — la libération du SID à la déconnexion (`session_id = None`, anti *ghost emits*) violait la contrainte `NOT NULL`.

#### Timers & machine à états

- **Rollback admin → timer Mode Libre complet** (`app/main.py`) — `_stop_quiz_with_rollback()` basculait en `LIBRE` sans armer `libre_ends_at`, ni le job de fin APScheduler, ni le tick serveur. Conséquences : Chuck Mode restait figé sur `--:--`, un refresh ne resynchronisait pas, et `/api/admin/adjust-timer` était un *no-op* (sa branche LIBRE exige `libre_ends_at`) jusqu'à ce qu'un `↺ Reset 15 min` arme le chrono. La fonction délègue désormais l'entrée en LIBRE à `_start_libre_phase(game, play_sound=False)`, produisant un état pleinement formé : `phase_changed` porte un `ends_at` valide, et admin / mobiles / projecteur affichent le même décompte avec des boutons `-5/+5/+10 min` opérationnels immédiatement. Remplace le correctif partiel (ajout d'un `ends_at` nul à l'emit). **[AXE 3]**

#### Listeners & payloads Socket.io

- **`new_scoop` sur `mobile.html`** — le flux de scoops temps réel était mort côté mobile (handler absent).
- **`quiz_ended` / `quiz_stopped` sur `admin.html`** — Chuck Mode restait aveugle après une fin de quiz naturelle ou un rollback d'urgence.
- **`latest_player` dans `answer_progress`** (`app/events.py`) — projecteur et admin ne pouvaient pas afficher le dernier répondant.

#### Visuel & sémantique

- **`var(-z-black)` → `var(--black)`** dans `.btn-gold` (`templates/admin.html`) — coquille CSS rendant le texte des boutons or invisible (noir sur noir). **[AXE 5]**
- **Sélecteur `.choice-key` → `.choice-letter`** dans le handler `question_answer` (`templates/admin.html`) — la coloration verte de la bonne réponse n'était jamais appliquée sur le panneau admin. **[AXE 5]**

### Changed

- **`_start_libre_phase(game, play_sound: bool = True)`** (`app/main.py`) — nouvel argument permettant de réutiliser l'entrée en LIBRE depuis le rollback sans déclencher l'ambiance sonore `phase2_start` (un arrêt manuel ne doit pas jouer de son).
- **Protocole `session_reset`** — `/api/admin/reset-scores` émet désormais `session_reset` avec le payload `{"keep_players": true}`. Les clients ne purgent leur token que lorsque le drapeau est absent/faux (reset complet) ; sinon ils conservent le token et se reconnectent directement dans le lobby.

---

## [v4.6.2] — 2026-05-19

VIP Mode complet pour Blair Waldorf — dashboard 4 tuiles accessible via `/vip`, isolé dans son propre template, zéro contamination de `mobile.html`.

### Added

#### Authentification & routage VIP

- **Décorateur `@vip_required`** (`app/main.py`) — protège toutes les routes `/vip` et `/api/vip/*`. Sans `session["blair_vip_verified"]`, redirige vers `/mobile`.
- **Route `GET /blair-vip`** — vérifie `BLAIR_VIP_TOKEN` dans le query string, pose la session VIP et redirige vers `/vip`. Remplace l'ancien comportement de redirection vers `/mobile?vip=blair`.
- **Route `GET /vip`** — sert `vip.html` (protégée par `@vip_required`).

#### Template `vip.html` (clone isolé de `mobile.html`)

- Clone complet de `mobile.html` — Blair joue le jeu normalement + overlay VIP additionnel.
- **Onglet 💎 VIP** dans la bottom nav, positionné à droite de l'onglet Score.
- **Overlay VIP** (`#vip-overlay`, `z-index: 300`) — monte par slide depuis le bas au tap sur l'onglet.
- **Système de panneaux extensible** (`VIP_PANEL_META` + `_showVipPanel(name)`) — ajouter une nouvelle tuile = 1 `<div id="vip-panel-X">` + 1 entrée dans `VIP_PANEL_META` + 1 branche `if (name === 'X')`.

#### 🐱 Tuile 1 — Dino Gallery (Step 2)

- Carrousel photo avec fetch `/api/vip/gallery`.
- Gestes swipe natif (touch start/end avec seuil 40 px).
- Transition en fondu entre slides ; indicateurs de points synchronisés.
- Fallback 3 emojis chats si `static/dino/` est vide.
- **Route `GET /api/vip/gallery`** — retourne les fichiers `*.jpg/*.png/*.webp` présents dans `static/dino/`.

#### 🔊 Tuile 2 — VIP Soundboard (Step 3)

- 6 boutons : Champagne 🥂 · Drama 🎭 · Gossip 🤫 · Scandale 😱 · Suspens ⏱ · Victoire 🏆.
- Lecture MP3 via `<audio>` si `static/sounds/vip/<id>.mp3` existe.
- Fallback Web Audio API (fréquence + forme d'onde distincte par bouton) si le fichier est absent.
- `sbStopAll()` interrompt tout son en cours avant d'en démarrer un nouveau.
- **Route `GET /api/vip/sounds`** — retourne la liste des sons avec URL MP3 ou `null` selon présence fichier.
- **Constante `VIP_SOUNDS`** (`app/main.py`) — 6 entrées `{id, name, icon}`, extensible sans modifier le template.

#### 📰 Tuile 3 — Scoops Manager (Step 4)

- Fetch et affichage de la liste des scoops publiés.
- Actions **Épingler** et **Supprimer** par scoop, avec feedback visuel immédiat.
- Toggle **QR Code** (base64 depuis `/api/qr-code` existant, sans nouvel endpoint).
- **Route `GET /api/vip/scoops`** — retourne les scoops non supprimés triés par date décroissante.
- **Route `POST /api/vip/scoops/<id>/pin`** — marque `pinned=True`, émet `scoop_pinned` sur tous les clients.
- **Route `POST /api/vip/scoops/<id>/delete`** — marque `deleted=True`, émet `scoop_deleted` sur tous les clients.

#### ✨ Tuile 4 — Custom Blast (Step 5)

- Formulaire de saisie dans l'overlay VIP pour composer un message officiel Gossip Girl.
- **Route `POST /api/vip/blast`** — enregistre le scoop en DB, émet `new_scoop` + `play_sound(new_gg)` + `vip_blast` (payload : texte + timestamp).
- **Overlay Blast** (`#blast-overlay`, `z-index: 600`) sur `vip.html` ET `mobile.html` — déclenché par `socket.on('vip_blast')`.
- Animation CSS wow-factor : burst de 8 particules (`@keyframes blastParticle`) avec vecteurs `--dx`/`--dy` en CSS custom properties, gem centrale (`@keyframes blastGemIn`), texte fade-up (`@keyframes blastFadeUp`), glow pulsant (`@keyframes blastGlow`).
- Auto-dismiss après 5 s ou clic.

#### Médias drop-in (aucune modification de code requise)

| Dossier | Contenu attendu | Effet |
|---|---|---|
| `static/dino/` | `*.jpg` / `*.png` / `*.webp` | Galerie photo réelle (fallback emojis si vide) |
| `static/sounds/vip/` | `champagne.mp3` · `drama.mp3` · `gossip.mp3` · `scandale.mp3` · `suspens.mp3` · `victoire.mp3` | Audio réel sur le Soundboard (fallback Web Audio API si absent) |

### Changed

- **`/blair-vip`** redirige désormais vers `/vip` (et non plus vers `/mobile?vip=blair`).
- **`mobile.html`** reçoit le CSS et le HTML de l'overlay Blast uniquement — nécessaire pour que tous les invités voient l'animation au `vip_blast`. Aucun autre changement.

---

## [v4.6.1] — 2026-05-18

Audit de fiabilité et de performance — corrections de bugs critiques identifiés après v4.6-bots.  
Aucune nouvelle fonctionnalité. Aucune régression comportementale.

### Fixed

| # | Fichier(s) | Description |
|---|-----------|-------------|
| C-1 | `app/main.py` | **Ghost timer tasks** — compteur de génération `_tick_task_generation` au niveau module. `_send_question()` incrémente le compteur et le passe à `_question_tick_task()` à la création. Chaque tick vérifie si sa génération correspond encore au compteur global — si non, la tâche fantôme se termine immédiatement sans émettre de `question_tick`. Élimine les double-décomptes lors d'un rechargement de question. |
| C-2 | `app/main.py` → `_libre_end_job` | **Objet SQLAlchemy périmé dans un job APScheduler** — `GameSession.query.get(game_id)` retournait parfois un objet issu du cache ORM d'une session précédente. Ajout de `db.session.refresh(game)` pour forcer une lecture fraîche en DB avant de vérifier `game.is_libre`. |
| C-3 | `app/main.py` → `_libre_tick_task` | **Requête DB à chaque seconde dans la boucle Mode Libre** — la vérification `GameSession.query.get()` était exécutée 60× par minute. Optimisation : vérification toutes les 10 secondes (`tick_count % 10 == 0`), réduisant les lectures DB de 90 %. |
| C-4/5 | `app/events.py` → `on_disconnect` | **Session leak sur déconnexion** — `player.session_id` n'était pas remis à `None` à la déconnexion, laissant un SID mort en base. Corrigé : `player.session_id = None` ajouté. Suppression d'un doublon de `socketio.emit("leaderboard_update", …)` en fin de handler. |
| C-6 | `app/models.py` | **SQLite en mode journal DELETE par défaut** — risque de contention sur plusieurs connexions simultanées. Activation du **WAL mode** via un listener `@event.listens_for(Engine, "connect")` : `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA synchronous=NORMAL`. S'applique à chaque connexion du pool sans modifier le schéma. |
| C-7 | `app/events.py` → `on_submit_answer` | **Race condition sur l'attribution des points** — `correct_count` était lu depuis `app.config["current_answers"]` (liste en mémoire) avant la fin de la vérification de toutes les réponses. Corrigé : lecture directe depuis `Score.query.filter_by(question_id=…, is_correct=True).count()` dans la même transaction. |
| C-8 | `app/models.py` + `app/main.py` | **`synchronize_session` non spécifié sur les bulk updates SQLAlchemy** — `Player.query.update({"score_total": 0, …})` sans `synchronize_session=False` peut lever une `InvalidRequestError` si des objets chargés sont en session. Ajout du flag sur tous les appels `Player.query.update()` (5 occurrences dans `main.py`, 1 dans `models.py`). |
| C-9/10 | `templates/mobile.html` | **Upload image bloquant** — `FileReader.readAsDataURL()` (synchrone, double allocation mémoire) remplacé par `URL.createObjectURL()` (aperçu instantané, zéro copie) + `Canvas.toBlob()` (compression async WebP non-bloquante). Ajout d'un `AbortController` dans `state._uploadAbort` pour annuler un upload en cours si le joueur change d'image. |
| C-11 | `templates/mobile.html` | **Timer fantôme côté client** — `setTimer()` et `_questionTimerInterval` (`setInterval` client) coexistaient avec les `question_tick` serveur, causant deux barres de progression indépendantes et des sauts visuels. Suppression complète du timer côté client. Le décompte est désormais 100 % piloté par les événements `question_tick` du serveur. Ajout d'un guard `data.question_id !== state.currentQuestion?.question_id` pour ignorer les ticks d'une question obsolète. |

### Removed

| Élément | Fichier | Raison |
|---------|---------|--------|
| `pick_questions()` | `app/models.py` | Fonction morte depuis v4.5 — jamais appelée après le passage à `pick_one_per_category()`. |
| `app.config["quiz_questions"]` | `app/main.py` | Clé initialisée à `[]` dans 5 endroits mais jamais lue (remplacée par `pick_one_per_category()` en v4.5). Toutes les initialisations supprimées. |
| `app.config["projector_sound_muted"]` | `app/main.py` | État write-only : écrit lors du mute projecteur mais jamais lu en dehors de l'assignation. Supprimé. |
| `from app.models import Score, Scoop as ScoopModel` (import local) | `app/main.py` → `api_reset_scores` | Import redondant déplacé dans les imports du module — l'import local à l'intérieur de la route masquait l'import de niveau module. |
| `from .main import _cancel_job, _question_timer_job` (import local) | `app/events.py` → `on_submit_answer` | Import déplacé au niveau module avec les autres imports de `.main`. |
| `templates/admin_old.html` | `templates/` | Template legacy de l'admin, non utilisé depuis v4.0. Supprimé via `git rm`. |
| `templates/admin_v4.5.html` | `templates/` | Snapshot de l'admin v4.5 laissé par erreur, non référencé par aucune route. Supprimé via `git rm`. |

---

## [v4.6-bots] — 2026-05-17

### Fixed

| # | Fichier(s) | Description |
|---|-----------|-------------|
| B-A | `app/bot_runner.py` | **Logs bots pas en temps réel** — ajout d'un buffer circulaire de 200 lignes (`_log_buffer`). `set_admin_sid()` rejoue le buffer complet vers le nouveau SID dès qu'un admin se connecte. Les logs émis avant l'ouverture de Chuck Mode ne sont plus perdus. |
| B-B | `templates/admin.html` | **Liste joueurs vide** — tous les `fetch()` vers les routes `@admin_required` manquaient un `r.ok` check et un `try/catch` autour de `r.json()`. Si la session Flask expirait, le serveur renvoyait un redirect 302 HTML, `r.json()` levait une `SyntaxError` silencieuse et le tableau restait vide. Corrigé sur 9 fonctions : `loadDashboardData`, `checkBlair`, `loadPlayers`, `loadScoops`, `loadScores`, `loadScoresHistory`, `loadLogs`, `refreshBotList`, `loadBotLogsFromServer`. |
| B-C | `app/events.py` + `templates/projector.html` | **Elite Circle vide dans le projecteur** — `on_projector_reconnect` n'émettait `leaderboard_update` que si le quiz était actif. Corrigé : émission systématique quelle que soit la phase (LOBBY, LIBRE, QUIZ). `player_joined` inclut maintenant le leaderboard dans son payload ; `projector.html` appelle `renderLeaderboard()` directement à la réception de `player_joined` sans attendre `leaderboard_update`. |

---

## [v4.5] — 2026-05-17

### Added

#### Système de questions (Chantier A — refonte complète)
- **`pick_one_per_category()`** — nouvelle fonction dans `models.py`. À chaque round, le serveur sélectionne 1 question par catégorie non épuisée et les soumet au GG comme candidates. Remplace l'ancien tirage aléatoire de 10 questions en début de quiz. 8 catégories × 259 questions.
- **Blacklist inter-rounds** — les candidates non choisies par le GG sont blacklistées au round suivant (`last_round_candidates` en mémoire), forçant la diversité et évitant les répétitions immédiates.
- **Shuffle double** — la liste globale est mélangée à chaque appel `_ask_gg_to_pick()`, et chaque pool par catégorie est mélangé indépendamment. Élimine le biais positionnel lié à l'ordre du fichier JSONL.
- **Auto-pick serveur** — si une seule candidate est disponible (pool quasi-épuisé), le serveur sélectionne automatiquement sans interaction GG.
- **Catégorie affichée dans le pick panel GG** — `mobile.html` affiche la catégorie de chaque candidate (`.gg-candidate-cat`) au-dessus de l'énoncé.
- **Countdown GG pick étendu** — 10s → **15s** pour le timer de sélection de question.
- **Route `POST /api/admin/reset-questions`** — remet à zéro `questions_asked` et `last_round_candidates` sans toucher aux scores ni aux joueurs. Bouton 🃏 dans la sidebar Chuck Mode.

#### Chuck Mode (Vague 1)
- **Route `POST /api/admin/refresh-player`** — émet un `client_reload` ciblé vers le socket d'un joueur spécifique. Bouton ↻ par ligne dans le tableau des joueurs.
- **Route `POST /api/admin/force-gg-quiz`** — coupe le Mode Libre en cours et émet `gg_quiz_ready` au GG pour lancer un quiz immédiatement. Remplace le bouton "Terminer Phase 2".
- **Bouton "🎯 Lancer Quiz"** — en Mode Libre, émet directement `gg_start_quiz` via socket, identique à l'action GG, sans bug de séquence.
- **Handler `admin_error` → toast** — le Chuck Mode affiche un toast d'erreur pour toute action refusée par le serveur (pause sans quiz actif, +10s sans timer actif, transfert GG → déjà GG, etc.).

#### Mobile (UX)
- **Handler `answer_error` → toast** — le mobile affiche un toast si le serveur refuse une réponse (double submit, GG tentant de répondre…).
- **Feedback immédiat au clic** — indicateur "⏳ Réponse envoyée…" affiché instantanément sans attendre le retour serveur. Le résultat vert/rouge et les points arrivent avec `answer_confirmed`.
- **Scroll automatique vers le résultat** — après avoir répondu, la zone feedback scrolle dans la vue sur les petits écrans.
- **Timer Mode Libre mis à jour** — le mobile écoute `timer_update` pour rafraîchir son décompte quand l'admin ajoute du temps.

#### Logique serveur
- **Délai Mode Libre calé sur l'audio `new_gg`** — constante `NEW_GG_AUDIO_DURATION = 6.06`. Délai calculé à `max(6.06 + 1.5, 5.0) = 7.56s` dans `_end_quiz()`, garantissant que l'animation Nouveau GG se termine avant l'entrée en Mode Libre.

### Fixed

| # | Fichier | Description |
|---|---------|-------------|
| B1 | `app/main.py` → `api_transfer_gg` | Bloquer le transfert GG vers un joueur déjà GG — garde ajouté, `admin_error` émis |
| B2 | `app/main.py` → `_pause_quiz` | Émettre `admin_error` si aucun quiz actif (au lieu d'échouer silencieusement) |
| B3 | `app/main.py` → `api_adjust_timer` | Retourner une erreur explicite si ni LIBRE ni QUIZ n'est actif |
| B4 | `templates/mobile.html` → `quiz_paused` | Overlay pause pour tous les joueurs : cas 1 (pick GG en cours → masque le pick panel) et cas 2 (question active → `div#quiz-pause-overlay` sur `quiz-active`) |
| B5 | `templates/mobile.html` → `question_answer` | Afficher `💤 Pas de réponse · La bonne réponse était : X` aux abstentionnistes |
| B6 | `templates/admin.html` → handler socket | L'ancien handler écoutait `question_result` (inexistant) au lieu de `question_answer` — renommé + coloration ✓ de la bonne réponse |
| B7 | `app/events.py` → `on_submit_answer` | All-In : comparer uniquement les réponses de joueurs encore connectés (`eligible_sids`) — un joueur déconnecté ne bloque plus le déclenchement anticipé |
| B8 | `templates/mobile.html` | NaN sur la barre de progression — division protégée par `typeof data.total === 'number'` |
| B9 | `templates/admin.html` | `pauseQuiz()` tronquée en pleine ligne (SyntaxError silencieuse rendant le dashboard inopérant) — fonction reconstruite |
| B10 | `app/events.py` | Handlers audio (`on_sound_stopped`, `on_projector_toggle_sound`, `on_projector_sound_state`) reconstruits après troncature du fichier à la ligne 604 |

### Changed
- **Flux quiz entièrement refondu** — `quiz_questions` (liste pré-sélectionnée en début de quiz) est abandonné. Les questions sont sélectionnées round par round via `pick_one_per_category()`.
- **`_send_question()`** — reçoit `[chosen_q]` + `index=0` (position dans la liste d'un élément). `game.question_index` porte le numéro de round réel ; le job APScheduler reçoit `index=game.question_index` pour que le guard anti-doublon reste opérationnel.
- **`on_gg_choose_question()`** — blackliste les non-choisis dans `last_round_candidates`, pose `game.question_index`, appelle `_send_question(game, [chosen_q], 0)`. Fin de la manipulation de l'ordre de `quiz_questions`.
- **`_resume_quiz()`** — mode question active utilise `game.get_current_question()` ; mode pick appelle `_ask_gg_to_pick(game, [], game.question_index)`.
- **`api_adjust_timer()` en mode QUIZ** — utilise `game.get_current_question()` pour retrouver le `question_id`, plus de dépendance à `quiz_questions`.
- **"Phase 2"** → **"Mode Libre"** — renommage sémantique dans tous les labels, boutons et sons de l'interface admin.
- **Personnage en or** — `.lb-full-char`, `.lb-mini-char` (mobile), `.lb-char` (projecteur), Elite Circle et historique des scores (admin) utilisent `var(--gold)`.

---

## [v4.4] — 2026-05-15 *(base stable de référence)*

### Added
- **Bouton "Relancer question"** — remplace le bouton "Passer". Relance la question active avec un timer frais de 10 s et remet les réponses à zéro. Élimine les bugs sonores causés par l'ancien skip. Présent dans Dashboard > Quiz en cours et Quiz Live > Contrôles Quiz.
- **Délai quiz_start avec confirmation GG** — au lancement d'un quiz, le GG voit un panneau "Je suis prêt — Choisir la question". Le sélecteur s'ouvre au clic ou automatiquement après 25 s (durée du son + marge).
- **QR Code toggle sur projecteur** — bouton dans Contrôles > QR Code et Actions rapides pour afficher / masquer le QR Code sur le grand écran.
- **Scores en grand sur projecteur** — overlay plein écran top 10, déclenché depuis Contrôles > Aperçu Projecteur et Quiz Live.
- **Bouton Refresh projecteur** — force un rechargement de la page projecteur depuis le Chuck Mode.
- **Bouton Stop son dans Actions rapides** — raccourci dashboard pour stopper le son en cours.
- **Bouton Pause/Relancer dans Quiz Live** — synchronisé avec le bouton du Dashboard.
- **Bouton "Supprimer" joueur** — suppression dure (Player + Scores en cascade), distinct d'Expulser.
- **Transfert GG silencieux** — flag `silent:true` pour réassigner le GG sans son ni overlay projecteur.
- **Overlay "Mode Libre"** — à l'entrée en LIBRE, overlay plein écran ~15 s sur le projecteur.
- **Bouton "Refresh mobiles"** — force `window.location.reload()` sur tous les clients mobiles.
- **Bouton "Reset Scores"** — remet les scores à zéro et supprime les scoops sans effacer les profils.

### Fixed
- **Projecteur : question disparaît après F5** — `projector_reconnect` ré-émet la question courante avec le temps restant réel calculé depuis `question_started_at`.
- **+10 secondes pendant le quiz** — `api_adjust_timer` distingue LIBRE (décale `libre_ends_at`) et QUIZ (reschedule le job APScheduler + émet `question_tick`).
- **Double countdown après +10s** — `_question_tick_task` calcule `remaining` dynamiquement depuis `next_run_time` APScheduler.
- **Son `quiz_start` joue au Resume** — `_resume_quiz` émet `phase_changed` avec `"resumed": True`, le projecteur ne rejoue pas l'intro.
- **Boutons quiz utilisables hors quiz** — classe `.quiz-only-btn` désactivée (opacity 0.35) hors phase QUIZ/QUIZ_PAUSED.
- **Doublon joueur après expulsion** — `join_player` fusionne sur le `personnage` si un profil hors-ligne existe déjà.
- **GG hors-ligne après rollback** — réassignation automatique au joueur connecté avec le meilleur score.

### Changed
- `_cancel_all_timers()` inclut `quiz_intro_timeout` et remet `quiz_intro_pending` à `False`.
- Route `/api/admin/skip-question` remplacée par `/api/admin/reload-question`.
- Sidebar Chuck Mode : deux boutons de reset distincts ("Reset Scores" et "Reset Complet").

---

## [v4.2] — sound_fixes — 2026-05

### Added
- Système audio complet côté projecteur (source de vérité unique).
- Overlay "Activer le son" au démarrage du projecteur (AudioContext unlock).
- Barres de progression des sons dans Contrôles > Sons.
- Bouton Stop son global dans Contrôles > Sons.
- Synchronisation son projecteur → admin via `sound_started` / `sound_stopped`.
- Route `/api/admin/stop-sound`.
- QR Code overlay (`/qr`) et route `/api/qr-code`.

### Fixed
- Doubles déclenchements sonores (race condition `_current_audio_el`).
- `stopCurrentSound()` stoppe tous les éléments `<audio>` de la page.

---

## [v4.1] — 2026-04

### Added
- Machine à états stricte : `LOBBY → QUIZ → QUIZ_PAUSED → LIBRE → QUIZ`.
- Pause / Reprise quiz (`PAUSE_QUIZ` / `RESUME_QUIZ`) avec sauvegarde du contexte.
- Stop quiz + rollback complet des scores (`STOP_QUIZ`).
- Mécanisme All-In : révélation anticipée si tous les joueurs ont répondu.
- Overlay pause sur projecteur et mobile.

### Fixed
- Thread-safety APScheduler : tous les jobs encapsulés dans `with app.app_context()`.
- Guard anti-doublon sur `_question_timer_job` (vérification `game.question_index`).

---

## [v4.0] — 2026-03

### Added
- Refonte complète de l'interface mobile (bottom nav, screens, dark theme luxe).
- Leaderboard temps réel (Elite Circle).
- Système de scoops : LOBBY (GG only), QUIZ (ticker), LIBRE (tout le monde).
- Scoop popup plein écran 8 s avec queue séquentielle.
- Bandeau ticker scoops pendant le quiz.
- Personnage Blair Waldorf avec slot VIP et code secret.
- Token de reconnexion persistant (`localStorage`).
- Overlay nouveau GG avec confettis.

---

## [v3.3] — stable — 2026-02

- Version stable de référence.
- Stack Flask + Flask-SocketIO + APScheduler + SQLite.
- Première implémentation du Chuck Mode (admin dashboard).
- Quiz QCM avec pick GG, timer 10 s, scoring rapide (3/2/1/0 pts).
