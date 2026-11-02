# -*- coding: utf-8 -*-
r"""Collega il bot Telegram a Praga Rock. Si lancia una volta sola.

Prima, su Telegram: apri @BotFather, scrivi /newbot, scegli un nome, copia il token.
Poi:  python setup_telegram.py

Il token si incolla qui nel terminale e finisce solo nei segreti di GitHub: non viene scritto
su nessun file e non passa dalla chat.
"""
import getpass
import subprocess
import sys
import time

try:
    import truststore  # sul PC Avast intercetta il TLS

    truststore.inject_into_ssl()
except ImportError:
    pass

import requests

REPO = "BigThursday/praga-rock"
WAIT_SECONDS = 300


def api(token, method, **data):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=45)
    except requests.RequestException as e:  # il messaggio dell'eccezione contiene il token
        sys.exit(f"Telegram non raggiungibile ({type(e).__name__}). Riprova tra poco.")
    body = r.json()
    if not body.get("ok"):
        if r.status_code == 401:
            sys.exit("Token non valido. Ricopialo da @BotFather e riprova.")
        if r.status_code == 409:
            sys.exit("Questo bot e' gia' usato da un altro programma. Creane uno nuovo con /newbot.")
        sys.exit(f"Telegram ha risposto: {body.get('description')}")
    return body["result"]


def find_chat(token):
    offset = None
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        args = {"timeout": 25}
        if offset:
            args["offset"] = offset
        for update in api(token, "getUpdates", **args):
            offset = update["update_id"] + 1
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private":
                return chat["id"], chat.get("first_name", "")
    sys.exit("Non ho visto nessun messaggio. Rilancia e premi Start sul bot.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = getpass.getpass("Incolla il token del bot e premi Invio (non si vede mentre incolli): ").strip()
    if not token:
        sys.exit("Nessun token ricevuto.")
    bot = api(token, "getMe")
    print(f"\nBot trovato: @{bot['username']}")
    print(f"Ora apri https://t.me/{bot['username']} e premi Start. Ti aspetto qui...\n")
    chat_id, name = find_chat(token)
    api(token, "sendMessage", chat_id=chat_id,
        text="🤘 Praga Rock collegato. Ti scrivo qui quando esce un nuovo concerto rock a Praga.")
    print(f"Messaggio di prova mandato a {name}. Controlla Telegram.")

    for secret, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", str(chat_id))):
        done = subprocess.run(["gh", "secret", "set", secret, "-R", REPO], input=value, text=True, capture_output=True)
        if done.returncode:
            sys.exit(f"Non riesco a salvare {secret} su GitHub: {done.stderr.strip()}")
    print("Salvato su GitHub. Da ora gli avvisi partono da soli.")


if __name__ == "__main__":
    main()
