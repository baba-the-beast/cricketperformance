import urllib.request
import threading
from webapp.app import app

def run_app():
    app.run(port=5001, debug=False, use_reloader=False)

threading.Thread(target=run_app, daemon=True).start()

import time
time.sleep(1)

req = urllib.request.Request("http://127.0.0.1:5001/api/matches")
with urllib.request.urlopen(req) as response:
    print(response.read()[:100])

req = urllib.request.Request("http://127.0.0.1:5001/api/players")
with urllib.request.urlopen(req) as response:
    print(response.read()[:100])
