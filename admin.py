#!/usr/bin/env python3
"""
OMX Admin CLI
- Uses the regular /login endpoint (requires an admin account)
- Uses admin endpoints present on the server:
    POST /admin/ban            -> {"username": "<user>"}
    POST /admin/unban          -> {"username": "<user>"}
    POST /admin/delete_user    -> {"username": "<user>"}
    POST /admin/broadcast      -> {"subject": "...", "message": "..."}
- All state is in-memory. Token stored in RAM only.
- Robust error handling / confirmations / clear output.
"""

from __future__ import annotations
import requests
import getpass
import sys
import time
import textwrap

# -------- Config --------
DEFAULT_SERVER = "http://omx.dedyn.io:30174"   # change if needed
TIMEOUT = 8  # seconds
API_PREFIXES = {
    "ban": "/admin/ban",
    "unban": "/admin/unban",
    "delete_user": "/admin/delete_user",
    "broadcast": "/admin/broadcast",
}

# -------- UI helpers --------
class C:
    HEADER = '\033[95m'; BLUE = '\033[94m'; CYAN = '\033[96m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'
    END = '\033[0m'; BOLD = '\033[1m'

def color(s, col=C.END):
    return f"{col}{s}{C.END}"

def printc(s, col=C.END):
    print(color(s, col))

def pause(msg="Press Enter to continue..."):
    try:
        input(color(msg, C.BLUE))
    except KeyboardInterrupt:
        print()

def confirm(prompt: str) -> bool:
    ans = input(color(prompt + " [y/N]: ", C.YELLOW)).strip().lower()
    return ans in ("y", "yes")

# -------- HTTP helper --------
class APIError(Exception):
    pass

class AdminClient:
    def __init__(self, server_url: str = DEFAULT_SERVER):
        self.server_url = server_url.rstrip("/")
        self.token: str | None = None
        self.username: str | None = None
        self.role: str | None = None

    def _url(self, path: str) -> str:
        return self.server_url + path

    def send_request(self, path: str, payload: dict | None = None, method: str = "POST", params: dict | None = None):
        url = self._url(path)
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            if method.upper() == "POST":
                r = requests.post(url, json=payload or {}, headers=headers, timeout=TIMEOUT)
            else:
                r = requests.get(url, params=params or {}, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise APIError(f"Network error: {e}")

        # try parse JSON
        try:
            data = r.json()
        except Exception:
            raise APIError(f"Invalid JSON response (status {r.status_code}) from {url}")

        # server returns JSON with ok:false for errors; treat >=400 also as error
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
            # forward server-side error dictionary/messages
            raise APIError(data)
        return data

    # -------- Auth --------
    def login(self) -> bool:
        printc("=== ADMIN LOGIN ===", C.HEADER)
        u = input("Username: ").strip()
        if not u:
            printc("Cancelled.", C.YELLOW)
            return False
        p = getpass.getpass("Password: ")
        try:
            resp = self.send_request("/login", {"username": u, "password": p}, method="POST")
        except APIError as e:
            printc(f"Login failed: {e}", C.RED)
            return False

        # ensure the response contains token and role
        token = resp.get("token")
        role = resp.get("role")
        if not token:
            printc("Login succeeded but server did not return token.", C.RED)
            return False
        if role != "admin":
            printc("That account is not an admin (role != 'admin').", C.RED)
            return False

        # success
        self.token = token
        self.username = u.lower()
        self.role = role
        printc(f"Admin logged in as {self.username}. Token stored in RAM.", C.GREEN)
        return True

    # -------- Admin actions --------
    def ban_user(self, target: str):
        target = (target or "").strip().lower()
        if not target:
            printc("No username provided.", C.YELLOW); return
        if target == self.username:
            printc("You cannot ban yourself.", C.RED); return
        if not confirm(f"Confirm ban user '{target}'?"):
            printc("Cancelled.", C.YELLOW); return
        try:
            resp = self.send_request(API_PREFIXES["ban"], {"username": target}, method="POST")
        except APIError as e:
            printc(f"Ban failed: {e}", C.RED); return
        printc(f"User '{target}' banned. Server response: {resp}", C.GREEN)

    def unban_user(self, target: str):
        target = (target or "").strip().lower()
        if not target:
            printc("No username provided.", C.YELLOW); return
        if not confirm(f"Confirm unban user '{target}'?"):
            printc("Cancelled.", C.YELLOW); return
        try:
            resp = self.send_request(API_PREFIXES["unban"], {"username": target}, method="POST")
        except APIError as e:
            printc(f"Unban failed: {e}", C.RED); return
        printc(f"User '{target}' unbanned. Server response: {resp}", C.GREEN)

    def delete_user(self, target: str):
        target = (target or "").strip().lower()
        if not target:
            printc("No username provided.", C.YELLOW); return
        if target == self.username:
            printc("You cannot delete your own admin account here. Use a different admin account.", C.RED); return
        if not confirm(f"*** PERMANENTLY DELETE user '{target}' and all their data? This cannot be undone. Confirm"): 
            printc("Cancelled.", C.YELLOW); return
        try:
            resp = self.send_request(API_PREFIXES["delete_user"], {"username": target}, method="POST")
        except APIError as e:
            printc(f"Delete failed: {e}", C.RED); return
        printc(f"User '{target}' deleted. Server response: {resp}", C.GREEN)

    def broadcast(self, subject: str, message: str):
        subject = (subject or "").strip()
        message = (message or "").strip()
        if not subject or not message:
            printc("Missing subject or message.", C.YELLOW); return
        printc(f"Broadcast Subject: {subject}", C.CYAN)
        for line in textwrap.wrap(message, width=78):
            print(line)
        if not confirm("Send this broadcast to ALL users?"):
            printc("Cancelled.", C.YELLOW); return
        try:
            resp = self.send_request(API_PREFIXES["broadcast"], {"subject": subject, "message": message}, method="POST")
        except APIError as e:
            printc(f"Broadcast failed: {e}", C.RED); return
        printc("Broadcast sent. Server response:", C.GREEN)
        print(resp)

    # optional convenience: try to call /admin/list_users if server provides it
    def list_users_if_available(self):
        # path many servers might not implement; call gracefully
        try:
            resp = self.send_request("/admin/list_users", method="GET")
        except APIError as e:
            printc("Server does not expose /admin/list_users (or error):", C.YELLOW)
            printc(f"  {e}", C.YELLOW)
            return
        users = resp.get("users") or resp.get("data") or []
        printc("=== Registered users ===", C.CYAN)
        for u in users:
            printc(f" - {u}", C.GREEN)

# -------- CLI --------
def main_menu(client: AdminClient):
    while True:
        printc("\n=== OMX ADMIN CLI ===", C.HEADER)
        printc(f"Admin: {client.username or 'NOT LOGGED IN'}", C.GREEN)
        printc("1) Ban user", C.CYAN)
        printc("2) Unban user", C.CYAN)
        printc("3) Delete user", C.CYAN)
        printc("4) Broadcast message", C.CYAN)
        printc("0) Quit", C.YELLOW)
        choice = input("Choice: ").strip()
        if choice == "0":
            printc("Bye.", C.GREEN); sys.exit(0)
        if choice == "1":
            u = input("Username to ban: ").strip()
            client.ban_user(u)
            pause()
        elif choice == "2":
            u = input("Username to unban: ").strip()
            client.unban_user(u)
            pause()
        elif choice == "3":
            u = input("Username to delete: ").strip()
            client.delete_user(u)
            pause()
        elif choice == "4":
            subj = input("Broadcast subject: ").strip()
            printc("Enter message. Finish with a single '.' on a line.", C.YELLOW)
            lines = []
            while True:
                try:
                    ln = input()
                except KeyboardInterrupt:
                    print(); break
                if ln == ".":
                    break
                lines.append(ln)
            msg = "\n".join(lines).strip()
            client.broadcast(subj, msg)
            pause()
        else:
            printc("Invalid choice.", C.RED)
            time.sleep(0.4)

# -------- Entry point --------
def parse_args_and_run():
    server = DEFAULT_SERVER
    if len(sys.argv) > 1:
        server = sys.argv[1]
    client = AdminClient(server)
    # login
    ok = client.login()
    if not ok:
        printc("Login failed - exiting.", C.RED)
        sys.exit(1)
    try:
        main_menu(client)
    except KeyboardInterrupt:
        print()
        printc("Interrupted. Bye.", C.YELLOW)
        sys.exit(0)

if __name__ == "__main__":
    parse_args_and_run()