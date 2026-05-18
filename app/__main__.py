# ============================================================
#  GOSSIP GIRL PARTY — Entrypoint module
#  python3 -m app.main
#
#  IMPORTANT : eventlet.monkey_patch() DOIT etre appele en premier,
#  avant tout autre import, pour que APScheduler (BackgroundScheduler)
#  et Flask-SocketIO cohabitent correctement sur les memes threads.
# ============================================================

import eventlet
eventlet.monkey_patch()   # patche socket, threading, time… avant tout le reste

import os
from .main import socketio, app
from . import events  # ← enregistre tous les handlers SocketIO

if __name__ == "__main__":
    port  = int(os.getenv("APP_PORT", 7777))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    print(f"XOXO — Gossip Girl Party démarré sur http://0.0.0.0:{port}  (debug={debug})")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
