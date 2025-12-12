#!/usr/bin/env python3

import os
import sys
import subprocess
import time
import glob
import shutil
import socket
import threading
import urllib.request

BLUE = "\033[34m"
CYAN = "\033[96m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloaded_packages")
LOCAL_DIR = os.path.join(BASE_DIR, "local_packages")
UPDATE_DIR = os.path.join(BASE_DIR, "update")
REQ_FILE = os.path.join(BASE_DIR, "requirements.txt")

APP_URL = "https://raw.githubusercontent.com/Virensahtiofficial/omx/main/app.py"
MAIN_URL = "https://raw.githubusercontent.com/Virensahtiofficial/omx/main/main.py"

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def get_terminal_size():
    try:
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except:
        return 80, 24

def center_text(text, width):
    return " " * max(0, (width // 2) - (len(text) // 2)) + text

def move_cursor(row: int, col: int = 1):
    print(f"\033[{row};{col}H", end="", flush=True)

def check_internet(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except:
        return False

def animated_loading(stop_event, term_width, loading_y, term_height, msg="Loading"):
    dots = 0
    while not stop_event.is_set():
        text = f"{BLUE}{msg}{'.' * (dots % 4)}{RESET}"
        move_cursor(loading_y, 1)
        print(center_text(text, term_width), end="", flush=True)
        time.sleep(0.4)
        dots += 1
    move_cursor(term_height, 1)

def download_file(url, target_path):
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read()
        with open(target_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"{RED}Failed to download {url}: {e}{RESET}")
        return False

def update_files():
    os.makedirs(UPDATE_DIR, exist_ok=True)
    clear_screen()
    term_width, term_height = get_terminal_size()
    title_y = term_height // 2
    loading_y = title_y + 1
    move_cursor(title_y, 1)
    print(center_text(f"{CYAN}{BOLD}Updating OMX Client...{RESET}", term_width), end="", flush=True)
    stop_event = threading.Event()
    loader_thread = threading.Thread(target=animated_loading,
                                     args=(stop_event, term_width, loading_y, term_height, "Updating"))
    loader_thread.start()
    try:
        download_file(APP_URL, os.path.join(UPDATE_DIR, "app.py"))
        download_file(MAIN_URL, os.path.join(UPDATE_DIR, "main.py"))
    finally:
        stop_event.set()
        loader_thread.join()
        time.sleep(0.5)
        shutil.copy(os.path.join(UPDATE_DIR, "app.py"), os.path.join(BASE_DIR, "app.py"))
        shutil.copy(os.path.join(UPDATE_DIR, "main.py"), os.path.join(BASE_DIR, "main.py"))
        clear_screen()

def get_required_packages():
    if os.path.exists(REQ_FILE):
        with open(REQ_FILE, "r") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        return []

REQUIRED_PACKAGES = get_required_packages()

def download_packages(packages, download_dir):
    os.makedirs(download_dir, exist_ok=True)
    if not check_internet():
        return
    for pkg in packages:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "download", pkg, "-d", download_dir, "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print(f"\n{RED}Failed to download {pkg}.{RESET}")
            sys.exit(1)

def install_from_download(download_dir, target_dir):
    os.makedirs(target_dir, exist_ok=True)
    files = glob.glob(os.path.join(download_dir, "*"))
    if not files:
        print(f"\n{RED}No downloaded packages found to install.{RESET}")
        sys.exit(1)
    for file in files:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", file,
                 "--target", target_dir,
                 "--break-system-packages",
                 "--upgrade",
                 "--no-deps",
                 "-q"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            print(f"\n{RED}Failed to install {file}.{RESET}")
            sys.exit(1)

def start_intro():
    clear_screen()
    term_width, term_height = get_terminal_size()
    title = f"{CYAN}{BOLD}OMX Mail Client Launcher{RESET}"
    title_y = term_height // 2
    loading_y = title_y + 1
    move_cursor(title_y, 1)
    print(center_text(title, term_width), end="", flush=True)
    stop_event = threading.Event()
    loader_thread = threading.Thread(target=animated_loading,
                                     args=(stop_event, term_width, loading_y, term_height))
    loader_thread.start()
    try:
        if check_internet():
            download_packages(REQUIRED_PACKAGES, DOWNLOAD_DIR)
            install_from_download(DOWNLOAD_DIR, LOCAL_DIR)
        else:
            if os.path.exists(DOWNLOAD_DIR) and os.listdir(DOWNLOAD_DIR):
                install_from_download(DOWNLOAD_DIR, LOCAL_DIR)
            else:
                stop_event.set()
                loader_thread.join()
                print(f"\n{RED}No internet and no downloaded packages available.{RESET}")
                sys.exit(1)
    finally:
        stop_event.set()
        loader_thread.join()
        time.sleep(0.5)
        clear_screen()

if __name__ == "__main__":
    if LOCAL_DIR not in sys.path:
        sys.path.insert(0, LOCAL_DIR)
    update_files()
    try:
        start_intro()
        import app
        app.load_config()
        SERVER_URL = app.CONFIG.get("server_url", app.DEFAULT_SERVER)
        app.main_menu()
    except ModuleNotFoundError as e:
        print(f"\n{RED}Error: Required package missing. {e}{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        app.printc("Interrupted. Bye.", app.C.YELLOW)
        sys.exit(0)