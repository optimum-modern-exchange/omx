#!/usr/bin/env python3
# OMX Mail Client

import os
import json
import getpass
import time
import textwrap
import sys
import re
import httpx
import colorama
import socket
from colorama import Fore, Style

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_DIR = os.path.join(BASE_DIR, "app_config_data")
os.makedirs(CONFIG_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "client_config.json")
CLIENT_VERSION = "v.1.0.1-stable"

# server als IP en poort
DEFAULT_SERVER_HOST = "omx.dedyn.io"
DEFAULT_SERVER_PORT = 30174

# handig combineren voor gebruik
DEFAULT_SERVER = f"http://{DEFAULT_SERVER_HOST}:{DEFAULT_SERVER_PORT}"
TIMEOUT = 6 
PAGE_SIZE = 12

# init Colorama
colorama.init(autoreset=True)

class C:
    HEADER = Fore.MAGENTA
    BLUE   = Fore.BLUE
    CYAN   = Fore.CYAN
    GREEN  = Fore.GREEN
    YELLOW = Fore.YELLOW
    RED    = Fore.RED
    END    = Style.RESET_ALL
    BOLD   = Style.BRIGHT

def color(msg, col=C.END):
    return f"{col}{msg}{C.END}"

def printc(msg, col=C.END):
    print(color(msg, col))

def pause(msg="Press enter to continue..."):
    try:
        input(color(msg, C.BLUE))
    except KeyboardInterrupt:
        print()

# ---------- config management ----------
CONFIG = {}

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                CONFIG = json.load(f)
        except Exception:
            CONFIG = {}
    else:
        CONFIG = {}

def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(CONFIG, f)
    except Exception as e:
        printc(f"Failed to save config: {e}", C.RED)

load_config()
SERVER_URL = CONFIG.get("server_url", DEFAULT_SERVER)

# ---------- utilities ----------
def parse_recipient_field(s: str):
    if not s:
        return []
    # split on comma or whitespace
    parts = []
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
    else:
        parts = [p.strip() for p in s.split()]
    parts = [p for p in parts if p]
    return parts
    
def ensure_logged_in():
    if CONFIG.get("token") and CONFIG.get("username"):
        return True
    return False

def require_login_flow():
    if ensure_logged_in():
        return True
    printc("You need to log in first.", C.YELLOW)
    time.sleep(1)
    user_login()
    return ensure_logged_in()

def pretty_mail_list(mails, start_index=1):
    mapping = {}
    for i, m in enumerate(mails, start_index):
        subj = m.get("subject") or "(no subject)"
        sender = m.get("from") or m.get("sender") or "(unknown)"
        ts = m.get("timestamp") or 0
        date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        printc(f"[{i}] {subj} | From: {sender} | {date}", C.CYAN)
        mapping[i] = m
    return mapping

def show_mail_detail(mail):
    clear_screen()
    printc(f"{C.BOLD}Mail ID:{C.END} {mail.get('id', '')}", C.CYAN)
    printc(f"{C.BOLD}From:  {C.END}{mail.get('from', '')}", C.CYAN)

    to = mail.get("to")
    to_display = ", ".join(to) if isinstance(to, list) else str(to or "")
    printc(f"{C.BOLD}To:    {C.END}{to_display}", C.CYAN)

    cc = mail.get("cc") or []
    cc_display = ", ".join(cc) if isinstance(cc, list) else str(cc)
    printc(f"{C.BOLD}CC:    {C.END}{cc_display}", C.CYAN)

    bcc = mail.get("bcc") or []
    bcc_display = ", ".join(bcc) if isinstance(bcc, list) else str(bcc)
    printc(f"{C.BOLD}BCC:   {C.END}{bcc_display}", C.CYAN)

    subject = mail.get("subject") or ""
    printc(f"{C.BOLD}Subject:{C.END} {subject}", C.GREEN)

    printc(f"{C.BOLD}Message:{C.END}", C.YELLOW)
    
    body = mail.get("message") or ""
    print(body)

    ts = mail.get("timestamp") or 0
    printc(f"{C.BOLD}Timestamp:{C.END} {time.ctime(ts)}", C.BLUE)
    printc("-" * 60, C.CYAN)
    
# ---------- Auth flows ----------
def user_register():
    clear_screen()
    printc("=== REGISTER ===", C.HEADER)
    while True:
        username = input("Choose username (alphanumeric, 3-20)\nEnter: ").strip()
        if not username:
            printc("Cancelled.", C.YELLOW); return
        password = getpass.getpass("Password (min 8): ")
        password2 = getpass.getpass("Repeat password: ")
        if password != password2:
            printc("Passwords don't match.", C.RED); continue
        ok, resp = send_request("/register", {"username": username, "password": password})
        if not ok:
            printc(f"Register failed: {resp}", C.RED)
            if input("Try again? (y/n): ").strip().lower() != "y":
                return
            continue
        printc("Registered OK — log in now.", C.GREEN)
        time.sleep(0.3)
        ok2, resp2 = send_request("/login", {"username": username, "password": password})
        if ok2 and resp2.get("token"):
            CONFIG["username"] = username
            CONFIG["token"] = resp2.get("token")
            CONFIG["password"] = password  # optional but handy for sensitive ops; you may remove if not wanted
            save_config()
            printc("Auto-logged in and token saved.", C.GREEN)
            pause()
            return
        else:
            printc("Auto-login failed. Please login manually.", C.YELLOW)
            pause()
            return

def user_login():
    clear_screen()
    printc("=== LOGIN ===", C.HEADER)
    username = input("Username: ").strip()
    if not username:
        printc("Cancelled.", C.YELLOW); return
    password = getpass.getpass("Password: ")
    ok, resp = send_request("/login", {"username": username, "password": password})
    if not ok:
        printc(f"Login failed: {resp}", C.RED)
        pause()
        return
    token = resp.get("token")
    if not token:
        printc("Login did not return token — server issue.", C.RED)
        pause(); return
    CONFIG["username"] = username
    CONFIG["token"] = token
    if input("Save password locally for account actions? (y/n): ").strip().lower() == "y":
        CONFIG["password"] = password
    else:
        CONFIG.pop("password", None)
    save_config()
    printc("Logged in — token saved.", C.GREEN)
    pause()

def user_logout():
    CONFIG.pop("token", None)
    CONFIG.pop("username", None)
    CONFIG.pop("password", None)
    save_config()
    printc("Logged out locally.", C.GREEN)
    pause()

# ---------- Mail operations ----------
def send_request(endpoint, payload):
    try:
        token = CONFIG.get("token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(f"http://{DEFAULT_SERVER_HOST}:{DEFAULT_SERVER_PORT}{endpoint}",
                            json=payload, headers=headers)
            r.raise_for_status()  # raise bij HTTP errors (4xx/5xx)
            resp = r.json()
            if resp.get("ok"):
                return True, resp
            else:
                # server gaf een foutmelding terug
                err_msg = resp.get("error") or "Unknown server error"
                return False, {"error": err_msg}
    except httpx.TimeoutException:
        return False, {"error": "Request timed out."}
    except httpx.RequestError as e:
        return False, {"error": f"Connection error: {str(e)}"}
    except ValueError:
        # JSON decode error
        return False, {"error": "Invalid response from server."}
    except Exception as e:
        return False, {"error": f"Unexpected error: {str(e)}"}
            
def action_send():
    if not require_login_flow():
        return
    sender = CONFIG.get("username")
    clear_screen()
    printc("=== SEND MAIL ===", C.HEADER)

    while True:
        to_raw = input("To (comma/space separated usernames)\nEnter: ").strip()
        to = parse_recipient_field(to_raw)
        if to:
            break
        printc("No recipients entered. Try again.", C.RED)

    cc = []
    if input("Add CC? (y/n): ").strip().lower() == "y":
        cc = parse_recipient_field(input("CC: ").strip())

    bcc = []
    if input("Add BCC? (y/n): ").strip().lower() == "y":
        bcc = parse_recipient_field(input("BCC: ").strip())

    subject = input("Subject: ").strip()

    printc("Type your message. Type '.done' on a new line to finish.\n", C.YELLOW)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == ".done":
            break
        lines.append(line)
    if not lines:
        printc("No message entered. Aborting.", C.RED)
        pause()
        return

    message = "\n".join(lines)

    printc("\n=== Preview ===", C.CYAN)
    printc(f"To: {', '.join(to)}")
    if cc: printc(f"CC: {', '.join(cc)}")
    if bcc: printc(f"BCC: {', '.join(bcc)}")
    printc(f"Subject: {subject}")
    printc("-" * 40)
    printc(message)
    printc("-" * 40)

    if input("Send mail? (y/n): ").strip().lower() != "y":
        printc("Cancelled.", C.YELLOW)
        pause()
        return

    payload = {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "message": message}
    ok, resp = send_request("/send", payload)
    if not ok:
        printc(f"Send failed: {resp}", C.RED)
    else:
        printc(f"Mail sent successfully. id={resp.get('mail_id')}", C.GREEN)
    pause()
    
def list_folder(folder, page=0):
    if not require_login_flow(): return []
    payload = {"folder": folder, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
    ok, resp = send_request("/fetch_mail", payload)
    if not ok:
        printc(f"Failed to fetch {folder}: {resp}", C.RED)
        return []
    mails = resp.get("mails", [])
    if not mails:
        printc("No mails.", C.YELLOW)
        return []
    pretty_mail_list(mails)
    return mails

def interactive_read(folder):
    if not require_login_flow():
        return

    page = 0
    while True:
        clear_screen()
        printc(f"=== {folder.upper()} (page {page+1}) ===", C.HEADER)
        mails = list_folder(folder, page)
        if not mails:
            choice = input("Back (b) or refresh (r)? ").strip().lower()
            if choice == "b":
                return
            else:
                continue

        printc("\nOptions: [n]ext page, [p]rev page, [o]pen <num>, [r]efresh, [b]ack", C.BLUE)
        cmd = input("Choice: ").strip().lower()

        if cmd == "n":
            page += 1
            continue
        if cmd == "p" and page > 0:
            page -= 1
            continue
        if cmd.startswith("o") or cmd.startswith("open"):
            parts = cmd.split()
            if len(parts) < 2:
                num = input("Open which number? ").strip()
            else:
                num = parts[1]
            if not num.isdigit():
                printc("Invalid number", C.RED)
                pause()
                continue
            idx = int(num) - 1
            if idx < 0 or idx >= len(mails):
                printc("Out of range", C.RED)
                pause()
                continue

            mail = mails[idx]
            clear_screen()
            show_mail_detail(mail)

            while True:
                if folder == "deleted":
                    printc("Actions: [d]elete, [r]ecover, [s]pam add sender, [b]ack", C.BLUE)
                else:
                    printc("Actions: [d]elete, [s]pam add sender, [b]ack", C.BLUE)
                act = input("Action: ").strip().lower()

                if act == "d":
                    mid = mail.get("id")
                    ok, resp = send_request("/delete_mail", {"mail_id": mid, "folder": folder})
                    if not ok:
                        printc(f"Delete failed: {resp}", C.RED)
                    else:
                        msg = "Moved to deleted." if folder != "deleted" else "Permanently deleted."
                        printc(msg, C.GREEN)
                    pause()
                    break

                elif act == "r" and folder == "deleted":
                    mid = mail.get("id")
                    ok, resp = send_request("/recover_mail", {"mail_id": mid})
                    if not ok:
                        printc(f"Recover failed: {resp}", C.RED)
                    else:
                        printc("Mail recovered to inbox.", C.GREEN)
                    pause()
                    break

                elif act == "s":
                    sender = mail.get("from")
                    ok, resp = send_request("/add_sender_to_spam", {"sender": sender})
                    if not ok:
                        printc(f"Add spam failed: {resp}", C.RED)
                    else:
                        printc("Sender added to your spam list.", C.GREEN)
                    pause()

                elif act == "b":
                    break
                else:
                    printc("Unknown action.", C.YELLOW)
            break

        elif cmd == "r":
            continue
        elif cmd == "b":
            return
        else:
            printc("Unknown command.", C.YELLOW)
            pause()
            
def action_recover():
    if not require_login_flow(): return
    ok, resp = send_request("/fetch_mail", {"folder": "deleted"})
    if not ok:
        printc(f"Failed: {resp}", C.RED); pause(); return
    mails = resp.get("mails", [])
    if not mails:
        printc("No deleted mails.", C.YELLOW); pause(); return
    pretty_mail_list(mails)
    choice = input("Select mail number to recover (or 0 to cancel): ").strip()
    if not choice.isdigit(): return
    n = int(choice)
    if n == 0: return
    idx = n - 1
    if idx < 0 or idx >= len(mails):
        printc("Out of range", C.RED); pause(); return
    mail = mails[idx]
    mid = mail.get("id")
    ok, resp = send_request("/recover_mail", {"mail_id": mid})
    if not ok:
        printc(f"Recover failed: {resp}", C.RED)
    else:
        printc("Recovered to inbox.", C.GREEN)
    pause()

# ---------- Search ----------
def action_search():
    if not require_login_flow(): return
    clear_screen()
    printc("=== SEARCH ===", C.HEADER)
    query = input("Query: ").strip()
    if not query:
        printc("Empty query", C.YELLOW); return
    folder = input("Folder (inbox/sent/deleted): ").strip() or "inbox"
    ok, resp = send_request("/search_mail", {"query": query, "folder": folder})
    if not ok:
        printc(f"Search failed: {resp}", C.RED); pause(); return
    results = resp.get("results", [])
    if not results:
        printc("No results.", C.YELLOW); pause(); return

    for i, r in enumerate(results, 1):
        subj = r.get("subject") or "(no subject)"
        sender = r.get("from") or r.get("sender") or "(unknown)"
        ts = r.get("timestamp") or 0
        printc(f"[{i}] {subj} | From: {sender} | {time.ctime(ts)}", C.CYAN)
        snippet = r.get("snippet")
        if snippet:
            for line in textwrap.wrap(snippet, width=78):
                print(line)
            print()
    pause()

# ---------- Account management ----------
def action_change_password():
    if not require_login_flow(): return
    clear_screen()
    printc("=== CHANGE PASSWORD ===", C.HEADER)
    old = getpass.getpass("Old password: ")
    new = getpass.getpass("New password: ")
    new2 = getpass.getpass("Repeat new: ")
    if new != new2:
        printc("New passwords don't match.", C.RED); pause(); return
    ok, resp = send_request("/change_password", {"old_password": old, "new_password": new})
    if not ok:
        printc(f"Change password failed: {resp}", C.RED)
    else:
        if CONFIG.get("password"):
            CONFIG["password"] = new
            save_config()
        printc("Password changed.", C.GREEN)
    pause()

def action_change_username():
    if not require_login_flow(): return
    clear_screen()
    printc("=== CHANGE USERNAME ===", C.HEADER)
    new_user = input("New username: ").strip()
    if not new_user:
        printc("Cancelled.", C.YELLOW); return
    pw = getpass.getpass("Current password (required): ")
    ok, resp = send_request("/change_username", {"new_username": new_user, "password": pw})
    if not ok:
        printc(f"Change username failed: {resp}", C.RED)
    else:
        old = CONFIG.get("username")
        CONFIG["username"] = new_user
        save_config()
        printc(f"Username changed {old} -> {new_user}", C.GREEN)
    pause()

def action_delete_account():
    if not require_login_flow(): return
    clear_screen()
    printc("=== DELETE ACCOUNT ===", C.RED)
    confirm = input("Type DELETE to permanently delete your account: ").strip()
    if confirm != "DELETE":
        printc("Cancelled.", C.YELLOW); return
    pw = getpass.getpass("Your password (required): ")
    ok, resp = send_request("/delete_account", {"password": pw})
    if not ok:
        printc(f"Delete account failed: {resp}", C.RED)
    else:
        printc("Account deleted on server. Removing local config.", C.GREEN)
        CONFIG.pop("username", None); CONFIG.pop("token", None); CONFIG.pop("password", None)
        save_config()
        pause()
        # exit client
        printc("Exiting...", C.YELLOW)
        time.sleep(0.8)
        sys.exit(0)

# ---------- Spam management ----------
def action_view_spam_list():
    if not require_login_flow(): return
    ok, resp = send_request("/fetch_mail", {"folder": "spam"})
    if not ok:
        printc(f"Failed to fetch spam folder: {resp}", C.RED); pause(); return
    mails = resp.get("mails", [])
    if not mails:
        printc("No spam mails.", C.YELLOW); pause(); return
    pretty_mail_list(mails)
    pause()

def action_add_spam_sender():
    if not require_login_flow(): return
    sender = input("Sender username to mark as spam for you: ").strip()
    if not sender:
        printc("Cancelled.", C.YELLOW); return
    ok, resp = send_request("/add_sender_to_spam", {"sender": sender})
    if not ok:
        printc(f"Add spam failed: {resp}", C.RED)
    else:
        printc("Sender added to your spam list.", C.GREEN)
    pause()

def action_remove_spam_sender():
    if not require_login_flow(): return
    sender = input("Sender username to remove from spam: ").strip()
    if not sender:
        printc("Cancelled.", C.YELLOW); return
    ok, resp = send_request("/delete_sender_from_spam", {"sender": sender})
    if not ok:
        printc(f"Remove spam failed: {resp}", C.RED)
    else:
        printc("Sender removed from your spam list.", C.GREEN)
    pause()

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")
    
def check_server():
    printc("Loading...", C.BLUE)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT))
        s.close()
        return True
    except Exception:
        printc("Error connecting to server. Aborting...", C.RED)
        time.sleep(1)
        sys.exit(1)

# ---------- Main menu ----------
def main_menu():
    while True:
        check_server()
        clear_screen()
        user = CONFIG.get("username")
        print(f"{C.BOLD}{C.BLUE}╔══════════════════════════════════╗{C.END}")
        print(f"{C.BOLD}{C.BLUE}║        OMX Mail Client           ║{C.END}")
        print(f"{C.BOLD}{C.BLUE}╚══════════════════════════════════╝{C.END}")
        if user:
            printc(f"Logged in as: {user}", C.GREEN)
        else:
            printc("Not logged in", C.YELLOW)
        printc("\nMain Menu:", C.CYAN)
        printc("[1] Login / Register", C.CYAN)
        printc("[2] Send Mail", C.CYAN)
        printc("[3] Inbox", C.CYAN)
        printc("[4] Sent", C.CYAN)
        printc("[5] Deleted (Trash)", C.CYAN)
        printc("[6] Spam folder", C.CYAN)
        printc("[7] Search", C.CYAN)
        printc("[8] Account settings", C.CYAN)
        printc("[0] Quit", C.YELLOW)
        printc("-" * 40, C.YELLOW)

        choice = input(f"{C.BLUE}Choice: {C.END}").strip()

        if choice == "1":
            clear_screen()
            printc("Login / Register Menu:", C.CYAN)
            printc("[1] Login", C.CYAN)
            printc("[2] Register", C.CYAN)
            printc("[3] Logout", C.CYAN)
            printc("[4] Back", C.YELLOW)
            c = input("Choice: ").strip()
            if c == "1":
                user_login()
            elif c == "2":
                user_register()
            elif c == "3":
                user_logout()
            else:
                continue

        elif choice == "2":
            action_send()

        elif choice == "3":
            interactive_read("inbox")

        elif choice == "4":
            interactive_read("sent")

        elif choice == "5":
            interactive_read("deleted")  # later kan je hier recover optie toevoegen

        elif choice == "6":
            clear_screen()
            printc("Spam Menu:", C.CYAN)
            printc("[1] View spam folder", C.CYAN)
            printc("[2] Add sender to spam", C.CYAN)
            printc("[3] Remove sender from spam", C.CYAN)
            printc("[4] Back", C.YELLOW)
            sub = input("Choice: ").strip()
            if sub == "1": action_view_spam_list()
            elif sub == "2": action_add_spam_sender()
            elif sub == "3": action_remove_spam_sender()
            else: continue

        elif choice == "7":
            action_search()

        elif choice == "8":
            clear_screen()
            printc("Account Settings:", C.CYAN)
            printc("[1] Change password", C.CYAN)
            printc("[2] Change username", C.CYAN)
            printc("[3] Delete account", C.RED)
            printc("[4] Back", C.YELLOW)
            c = input("Choice: ").strip()
            if c == "1": action_change_password()
            elif c == "2": action_change_username()
            elif c == "3": action_delete_account()
            else: continue

        elif choice == "0":
            printc("App exited", C.GREEN)
            sys.exit(0)

        else:
            printc("Invalid choice.", C.RED)
            time.sleep(0.7)