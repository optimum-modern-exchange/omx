#!/usr/bin/env python3
# OMX Launcher - advanced, robust, secure
# Features:
# - safe update of requirements/app/main with backups + rollback
# - skip downloads/installs when local packages already present
# - verify downloads with optional .sha256 files if available
# - non-destructive pip install to local target
# - resilient to network issues, EOFError, KeyboardInterrupt
# - logging, silent/verbose modes, CLI flags
# - atomic file writes, thread-safe loader

from __future__ import annotations
import os
import sys
import subprocess
import time
import glob
import shutil
import threading
import urllib.request
import hashlib
import tempfile
import argparse
import importlib.util
from typing import Optional

# -------- console colors --------
BLUE = "\033[34m"
CYAN = "\033[96m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
RESET = "\033[0m"

# -------- paths & urls --------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloaded_packages")
LOCAL_DIR = os.path.join(BASE_DIR, "local_packages")
UPDATE_DIR = os.path.join(BASE_DIR, "update")
REQ_FILE = os.path.join(BASE_DIR, "requirements.txt")

APP_URL = "https://raw.githubusercontent.com/optimum-modern-exchange/omx/refs/heads/main/app.py"
MAIN_URL = "https://raw.githubusercontent.com/optimum-modern-exchange/omx/refs/heads/main/main.py"
REQ_URL = "https://raw.githubusercontent.com/optimum-modern-exchange/omx/refs/heads/main/requirements.txt"

LOG_PATH = os.path.join(BASE_DIR, "launcher_update.log")
TIMEOUT = 8  # seconds for network ops
LOADER_JOIN_TIMEOUT = 2.0

# -------- runtime flags (set by CLI) --------
FLAGS = {
    "silent": False,
    "no_update": False,
    "force_update": False,
    "verbose": False
}

# -------- utils --------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass
    if FLAGS.get("verbose") and not FLAGS.get("silent"):
        try:
            print(f"{CYAN}[LOG]{RESET} {msg}")
        except Exception:
            pass

def safe_print(*args, **kwargs):
    if FLAGS.get("silent"):
        return
    try:
        print(*args, **kwargs)
    except Exception:
        pass

def clear_screen():
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass

def get_terminal_size():
    try:
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except Exception:
        return 80, 24

def center_text(text: str, width: int) -> str:
    return " " * max(0, (width // 2) - (len(strip_ansi(text)) // 2)) + text

def strip_ansi(s: str) -> str:
    # minimal ANSI stripper for width calculations
    import re
    return re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', s)

def move_cursor(row: int, col: int = 1):
    try:
        print(f"\033[{row};{col}H", end="", flush=True)
    except Exception:
        pass

# -------- network check (robust) --------
def check_internet(timeout: int = TIMEOUT) -> bool:
    """Try to open a short HEAD to github - more reliable than raw socket."""
    try:
        req = urllib.request.Request("https://github.com", method="HEAD", headers={"User-Agent": "OMX-Launcher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception as e:
        log(f"check_internet failed: {e}")
        return False

# -------- animated loader thread --------
def animated_loading(stop_event: threading.Event, term_width: int, loading_y: int, term_height: int, msg: str = "Loading"):
    dots = 0
    try:
        while not stop_event.is_set():
            text = f"{BLUE}{msg}{'.' * (dots % 4)}{RESET}"
            move_cursor(loading_y, 1)
            try:
                print(center_text(text, term_width), end="", flush=True)
            except Exception:
                pass
            time.sleep(0.35)
            dots += 1
        move_cursor(term_height, 1)
    except Exception as e:
        log(f"animated_loading exception: {e}")

# -------- file utils --------
def file_sha256(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        log(f"file_sha256 error {path} {e}")
        return None

def download_url_to_file(url: str, dest: str, timeout: int = TIMEOUT) -> bool:
    """
    Download to tmp file, verify if identical then replace atomically.
    Returns True on success (or if identical), False on error.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OMX-Launcher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                log(f"download failed {url} status {getattr(resp, 'status', 'unknown')}")
                return False
            data = resp.read()
    except Exception as e:
        log(f"download error {url} {e}")
        return False

    try:
        # if dest exists and identical, skip replace
        if os.path.exists(dest):
            existing_hash = file_sha256(dest)
            tmp_hash = hashlib.sha256(data).hexdigest()
            if existing_hash and tmp_hash == existing_hash:
                log(f"download identical, skipping replace {url}")
                return True
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(dest))
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, dest)
        log(f"downloaded {url} -> {dest}")
        return True
    except Exception as e:
        log(f"download write error {url} -> {dest} {e}")
        # try remove tmp if exists
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False

def try_download_optional_hash(url: str, dest: str) -> bool:
    """
    If remote .sha256 exists next to url, download and verify dest.
    Returns True if either no remote hash or hash matches; False if hash present and doesn't match.
    """
    # construct sha url heuristically: url + ".sha256" or replace extension
    tried = []
    base_sha1 = url + ".sha256"
    base_sha2 = url + ".sha256sum"
    for sha_url in (base_sha1, base_sha2):
        tried.append(sha_url)
        try:
            req = urllib.request.Request(sha_url, headers={"User-Agent": "OMX-Launcher/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if getattr(resp, "status", 200) != 200:
                    continue
                txt = resp.read().decode("utf-8", errors="ignore").strip()
                # extract first hex-looking token
                import re
                m = re.search(r'([a-fA-F0-9]{64})', txt)
                if not m:
                    continue
                remote_hash = m.group(1).lower()
                # compute local file hash
                if not os.path.exists(dest):
                    log(f"local file missing for hash verify: {dest}")
                    return False
                local_hash = file_sha256(dest)
                if not local_hash:
                    return False
                if local_hash.lower() == remote_hash:
                    log(f"hash ok for {dest}")
                    return True
                else:
                    log(f"hash mismatch for {dest} (local {local_hash} != remote {remote_hash})")
                    return False
        except Exception:
            continue
    # no remote sha found, treat as okay (warn)
    log(f"no remote sha found for {url} (tried: {tried})")
    return True

# -------- local packages check --------
def local_packages_ready() -> bool:
    """Return True if LOCAL_DIR seems to contain installed packages."""
    try:
        if not os.path.isdir(LOCAL_DIR):
            return False
        # require at least one .dist-info or top-level package folder
        for f in os.listdir(LOCAL_DIR):
            if f.endswith(".dist-info") or f.endswith(".egg-info"):
                return True
            # top-level package folder or .py file
            if os.path.isdir(os.path.join(LOCAL_DIR, f)) or f.endswith(".py"):
                return True
        return False
    except Exception as e:
        log(f"local_packages_ready error {e}")
        return False

# -------- pip download & install --------
def download_packages(packages: list[str]) -> bool:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if not packages:
        log("no packages requested")
        return True

    # if local packages already present, skip downloads entirely
    if local_packages_ready():
        log("local packages already installed, skipping download")
        return True

    if not check_internet():
        log("no internet, skipping package download")
        return False

    for pkg in packages:
        try:
            cmd = [sys.executable, "-m", "pip", "download", pkg, "-d", DOWNLOAD_DIR, "-q"]
            log(f"running: {' '.join(cmd)}")
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log(f"downloaded package {pkg}")
        except subprocess.CalledProcessError as e:
            log(f"pip download failed {pkg} {e}")
            return False
    return True

def install_from_download() -> bool:
    # if already installed, skip
    if local_packages_ready():
        log("local packages already present, skipping install")
        # ensure in sys.path
        if LOCAL_DIR not in sys.path:
            sys.path.insert(0, LOCAL_DIR)
        return True

    os.makedirs(LOCAL_DIR, exist_ok=True)
    files = glob.glob(os.path.join(DOWNLOAD_DIR, "*"))
    if not files:
        log("no downloaded files to install")
        return False

    for file in files:
        try:
            cmd = [
                sys.executable, "-m", "pip", "install", file,
                "--target", LOCAL_DIR,
                "--upgrade",
                "--no-deps",
                "-q"
            ]
            log(f"running: {' '.join(cmd)}")
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log(f"installed {file} to {LOCAL_DIR}")
        except subprocess.CalledProcessError as e:
            log(f"pip install failed {file} {e}")
            return False

    # add local packages to sys.path
    if LOCAL_DIR not in sys.path:
        sys.path.insert(0, LOCAL_DIR)
    return True

# -------- safe copy & backup helpers --------
def safe_copy(src: str, dst: str) -> bool:
    try:
        tmp = dst + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        return True
    except Exception as e:
        log(f"safe_copy error {src} -> {dst} {e}")
        return False

def backup_file(path: str) -> Optional[str]:
    try:
        if not os.path.exists(path):
            return None
        bak = path + ".bak"
        shutil.copyfile(path, bak)
        log(f"backup created {bak}")
        return bak
    except Exception as e:
        log(f"backup_file error {path} {e}")
        return None

def restore_backup(path: str) -> bool:
    try:
        bak = path + ".bak"
        if os.path.exists(bak):
            safe_copy(bak, path)
            log(f"restored backup {bak} -> {path}")
            return True
        return False
    except Exception as e:
        log(f"restore_backup error {path} {e}")
        return False

# -------- dynamic import test (does not pollute sys.modules) --------
def test_import_module_from_path(path: str) -> bool:
    """
    Try to import the module from given path in isolation. Returns True if import succeeded.
    Does not add to sys.modules under 'app' name.
    """
    try:
        spec = importlib.util.spec_from_file_location("omx_update_test", path)
        if spec is None or spec.loader is None:
            log(f"spec_from_file_location failed for {path}")
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        log(f"test import OK for {path}")
        return True
    except Exception as e:
        log(f"test import failed for {path}: {e}")
        return False

# -------- update logic with safety & rollback --------
def update_files(force: bool = False) -> None:
    """
    Update requirements/app/main from remote if necessary.
    Uses backups and verifies importability of new app before committing.
    """
    if FLAGS.get("no_update") and not force:
        log("updates disabled by flag")
        return

    os.makedirs(UPDATE_DIR, exist_ok=True)
    clear_screen()
    term_width, term_height = get_terminal_size()
    title_y = term_height // 2
    loading_y = title_y + 1
    move_cursor(title_y, 1)
    safe_print(center_text(f"{CYAN}{BOLD}Checking for updates...{RESET}", term_width), end="", flush=True)

    stop_event = threading.Event()
    loader_thread = threading.Thread(target=animated_loading, args=(stop_event, term_width, loading_y, term_height, "Updating"))
    loader_thread.daemon = True
    loader_thread.start()

    req_path = os.path.join(UPDATE_DIR, "requirements.txt")
    app_path = os.path.join(UPDATE_DIR, "app.py")
    main_path = os.path.join(UPDATE_DIR, "main.py")

    try:
        internet = check_internet()
        if not internet:
            log("no internet available for updates")
            # if no internet but local requirements already exist, skip gracefully
            if os.path.exists(REQ_FILE):
                log("using existing local requirements.txt")
            else:
                log("no local requirements.txt found; skipping updates")
                return

        # download requirements if internet
        req_ok = False
        if internet:
            req_ok = download_url_to_file(REQ_URL, req_path)
            if not req_ok:
                log("requirements download failed")
        # if didn't download but local exists, use local
        if not req_ok and os.path.exists(REQ_FILE):
            log("using existing requirements.txt")
            req_ok = True

        if req_ok and os.path.exists(req_path):
            # only replace if different (download_url_to_file already does identical skip)
            safe_copy(req_path, REQ_FILE)
            log("requirements updated/ensured")

        # if no internet and no local packages we just return; packages handled later
        # now attempt to download code files (only if internet)
        app_ok = False
        main_ok = False
        if internet:
            # download updated code to temporary update dir
            app_ok = download_url_to_file(APP_URL, app_path)
            main_ok = download_url_to_file(MAIN_URL, main_path)

        # decide whether to apply updates: only if downloads succeeded or force
        apply_app = app_ok or force or (not internet and os.path.exists(os.path.join(BASE_DIR, "app.py")))
        apply_main = main_ok or force or (not internet and os.path.exists(os.path.join(BASE_DIR, "main.py")))

        # backup originals
        app_orig = os.path.join(BASE_DIR, "app.py")
        main_orig = os.path.join(BASE_DIR, "main.py")
        app_backup = backup_file(app_orig) if os.path.exists(app_orig) else None
        main_backup = backup_file(main_orig) if os.path.exists(main_orig) else None

        # apply updates to working dir but test before finalizing
        applied_any = False
        try:
            if apply_app and os.path.exists(app_path):
                safe_copy(app_path, app_orig)
                applied_any = True
                log("applied app.py update to working dir")
            if apply_main and os.path.exists(main_path):
                safe_copy(main_path, main_orig)
                applied_any = True
                log("applied main.py update to working dir")

            # optional remote hash verification for safety
            if internet and os.path.exists(app_orig):
                ok_hash = try_download_optional_hash(APP_URL, app_orig)
                if not ok_hash:
                    raise RuntimeError("app.py hash verification failed")
            if internet and os.path.exists(main_orig):
                ok_hash = try_download_optional_hash(MAIN_URL, main_orig)
                if not ok_hash:
                    raise RuntimeError("main.py hash verification failed")

            # test import of app.py to ensure it doesn't crash on import
            if os.path.exists(app_orig):
                if not test_import_module_from_path(app_orig):
                    raise RuntimeError("import test failed for updated app.py")

            # if everything ok commit (backups already exist); remove backup files optionally
            log(f"update commit successful, app_ok={app_ok} main_ok={main_ok}")
        except Exception as e:
            log(f"update failed during apply/test: {e}")
            # attempt rollback
            if app_backup:
                restore_backup(app_orig)
            if main_backup:
                restore_backup(main_orig)
            log("rolled back to backups after failed update")
            raise
    finally:
        stop_event.set()
        loader_thread.join(timeout=LOADER_JOIN_TIMEOUT)
        time.sleep(0.15)
        clear_screen()

# -------- requirements reader --------
def read_requirements() -> list[str]:
    if not os.path.exists(REQ_FILE):
        return []
    try:
        with open(REQ_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        pkgs: list[str] = []
        for l in lines:
            if l.startswith("-r ") or l.startswith("--requirement"):
                continue
            pkgs.append(l)
        return pkgs
    except Exception as e:
        log(f"read_requirements error {e}")
        return []

# -------- startup intro & install flow --------
def start_intro_and_install():
    clear_screen()
    term_width, term_height = get_terminal_size()
    title = f"{CYAN}{BOLD}OMX Mail Client Launcher{RESET}"
    title_y = term_height // 2
    loading_y = title_y + 1
    move_cursor(title_y, 1)
    safe_print(center_text(title, term_width), end="", flush=True)

    stop_event = threading.Event()
    loader_thread = threading.Thread(target=animated_loading, args=(stop_event, term_width, loading_y, term_height, "Preparing"))
    loader_thread.daemon = True
    loader_thread.start()

    try:
        pkgs = read_requirements()
        if local_packages_ready():
            log("local packages already installed, skipping download & install")
            if LOCAL_DIR not in sys.path:
                sys.path.insert(0, LOCAL_DIR)
        elif pkgs:
            internet = check_internet()
            if not internet:
                # no internet & no local packages -> error
                log("no internet and no local packages; cannot install requirements")
                # leave loader running briefly to show message, then exit
                stop_event.set()
                loader_thread.join(timeout=LOADER_JOIN_TIMEOUT)
                clear_screen()
                safe_print(f"{RED}Error: no internet and required packages not available locally.{RESET}")
                raise SystemExit(1)
            download_ok = download_packages(pkgs)
            if download_ok:
                install_ok = install_from_download()
                if install_ok:
                    if LOCAL_DIR not in sys.path:
                        sys.path.insert(0, LOCAL_DIR)
                    log("local packages installed and added to sys.path")
                else:
                    log("install_from_download failed, will attempt to continue using system packages")
                    # try continue (not ideal)
            else:
                log("download_packages failed")
                # try continue if local packages present
                if local_packages_ready():
                    if LOCAL_DIR not in sys.path:
                        sys.path.insert(0, LOCAL_DIR)
                    log("using existing local packages despite download failure")
                else:
                    stop_event.set()
                    loader_thread.join(timeout=LOADER_JOIN_TIMEOUT)
                    clear_screen()
                    safe_print(f"{RED}Error: failed to download required packages and no local fallback.{RESET}")
                    raise SystemExit(1)
        else:
            log("no requirements specified; skipping package install")
    finally:
        stop_event.set()
        loader_thread.join(timeout=LOADER_JOIN_TIMEOUT)
        time.sleep(0.12)
        clear_screen()

# -------- main launcher entry --------
def run_launcher():
    # add local packages to path if available
    if LOCAL_DIR not in sys.path:
        sys.path.insert(0, LOCAL_DIR)

    # perform updates (unless disabled)
    try:
        update_files(force=FLAGS.get("force_update", False))
    except Exception as e:
        log(f"update_files raised: {e}")
        # continue, because we may still run with existing files

    # install packages / prepare env
    try:
        start_intro_and_install()
    except SystemExit:
        raise
    except Exception as e:
        log(f"start_intro_and_install raised: {e}")

    # import app safely and run it
    try:
        import importlib
        if "app" in sys.modules:
            del sys.modules["app"]
        spec = importlib.util.find_spec("app")
        if spec is None:
            # try to import by path
            app_path = os.path.join(BASE_DIR, "app.py")
            if not os.path.exists(app_path):
                raise ModuleNotFoundError("app.py not found in BASE_DIR")
            spec = importlib.util.spec_from_file_location("app", app_path)
            app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app)  # type: ignore
        else:
            app = importlib.import_module("app")
        # best-effort load config
        try:
            if hasattr(app, "load_config"):
                app.load_config()
            if hasattr(app, "CONFIG") and hasattr(app, "DEFAULT_SERVER"):
                app.SERVER_URL = app.CONFIG.get("server_url", app.DEFAULT_SERVER)
            elif hasattr(app, "CONFIG"):
                app.SERVER_URL = app.CONFIG.get("server_url", "http://omx.dedyn.io:30174")
        except Exception as e:
            log(f"app config load failed {e}")
        # run app entrypoint
        if hasattr(app, "main_menu"):
            try:
                app.main_menu()
            except Exception as e:
                log(f"app.main_menu crashed: {e}")
                raise
        elif hasattr(app, "main"):
            try:
                app.main()
            except Exception as e:
                log(f"app.main crashed: {e}")
                raise
        else:
            log("no entry point found in app")
    except ModuleNotFoundError as e:
        safe_print(f"\n{RED}Error: Required module 'app' missing or import failed. {e}{RESET}")
        log(f"ModuleNotFoundError {e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        safe_print("\n" + YELLOW + "Interrupted. Bye." + RESET)
        raise SystemExit(0)
    except Exception as e:
        safe_print(f"\n{RED}Launcher fatal error: {e}{RESET}")
        log(f"fatal error {e}")
        raise SystemExit(1)

# -------- CLI parsing --------
def parse_args():
    p = argparse.ArgumentParser(description="OMX Launcher")
    p.add_argument("--silent", action="store_true", help="suppress stdout (still logs to file)")
    p.add_argument("--no-update", action="store_true", help="skip update check")
    p.add_argument("--force-update", action="store_true", help="force apply updates even if same")
    p.add_argument("--verbose", action="store_true", help="verbose logging to console")
    return p.parse_args()

# -------- safe main wrapper --------
def main():
    args = parse_args()
    FLAGS["silent"] = bool(args.silent)
    FLAGS["no_update"] = bool(args.no_update)
    FLAGS["force_update"] = bool(args.force_update)
    FLAGS["verbose"] = bool(args.verbose)

    try:
        run_launcher()
    except EOFError:
        safe_print("\n" + YELLOW + "Input closed (EOF). Exiting." + RESET)
        log("exited on EOFError")
        sys.exit(0)
    except KeyboardInterrupt:
        safe_print("\n" + YELLOW + "Interrupted by user. Exiting." + RESET)
        log("exited on KeyboardInterrupt")
        sys.exit(0)
    except SystemExit as e:
        # allow normal exits with status
        raise
    except Exception as e:
        safe_print(f"\n{RED}Unhandled launcher exception: {e}{RESET}")
        log(f"unhandled exception in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            safe_print("\n" + YELLOW + "Interrupted. Bye." + RESET)
        except Exception:
            pass
        sys.exit(0)