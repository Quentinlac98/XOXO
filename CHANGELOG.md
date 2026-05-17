# Changelog — XOXO : The Gossip Girl App

Toutes les modifications notables sont documentées ici.  
Format : [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/)  
XOXO, Chuck Bass.

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
- **Flux quiz entièrement refondu** — `quiz_questions` (liste pré-sélectionnée en début de quiz) est abandonné. Les questions sont sélectionnées round par round via `pick_one_per_category()`. `quiz_questions` reste initialisé à `[]` pour compatibilité rétro.
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
