#!/usr/bin/env python3
from __future__ import annotations
import sys
import asyncio
import argparse
import getpass
import json
import time
import random
import textwrap
import httpx
from typing import Optional

class C:
    HEADER = '\033[95m'; BLUE = '\033[94m'; CYAN = '\033[96m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'
    END = '\033[0m'; BOLD = '\033[1m'

def color(s: str, col: str = C.END) -> str:
    return f"{col}{s}{C.END}"

def prin(s: str, col: str = C.END) -> None:
    print(color(s, col))

def confirm(prompt: str) -> bool:
    try:
        r = input(color(prompt + " [y/N]: ", C.YELLOW)).strip().lower()
    except KeyboardInterrupt:
        print()
        return False
    return r in ("y", "yes")

class APIError(Exception):
    def __init__(self, detail):
        super().__init__(str(detail))
        self.detail = detail

class HTTPXAdminClient:
    def __init__(self, base_url: str, timeout: int = 8, retries: int = 3, backoff: float = 0.4):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.backoff = float(backoff)
        self.token: Optional[str] = None
        self.username: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    def _url(self, path: str) -> str:
        return self.base_url + path

    async def _client_ctx(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _request(self, path: str, method: str = "POST", json_payload: dict | None = None, params: dict | None = None):
        async def do_once():
            client = await self._client_ctx()
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            try:
                if method.upper() == "POST":
                    r = await client.post(self._url(path), json=json_payload or {}, headers=headers)
                else:
                    r = await client.get(self._url(path), params=params or {}, headers=headers)
            except httpx.RequestError as e:
                raise APIError(f"request error: {e}") from e
            try:
                data = r.json()
            except Exception:
                raise APIError(f"invalid json from server (status {r.status_code})")
            if r.status_code >= 400:
                raise APIError({"status": r.status_code, "body": data})
            if isinstance(data, dict) and data.get("ok") is False:
                raise APIError(data)
            return data

        last_exc = None
        for attempt in range(self.retries):
            try:
                return await do_once()
            except APIError as e:
                last_exc = e
                if attempt + 1 >= self.retries:
                    raise
                await asyncio.sleep(self.backoff * (2 ** attempt) + random.random() * 0.1)
        raise last_exc

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def login(self, username: str, password: str):
        payload = {"username": username, "password": password}
        resp = await self._request("/login", "POST", json_payload=payload)
        token = resp.get("token")
        role = resp.get("role")
        if not token:
            raise APIError("no token returned by server")
        self.token = token
        self.username = username.lower()
        return {"token": token, "role": role, "expires": resp.get("expires")}

    # existing admin ops
    async def ban_user(self, target: str):
        payload = {"username": target}
        return await self._request("/admin/ban", "POST", json_payload=payload)

    async def unban_user(self, target: str):
        payload = {"username": target}
        return await self._request("/admin/unban", "POST", json_payload=payload)

    async def delete_user(self, target: str):
        payload = {"username": target}
        return await self._request("/admin/delete_user", "POST", json_payload=payload)

    async def broadcast(self, subject: str, message: str):
        payload = {"subject": subject, "message": message}
        return await self._request("/admin/broadcast", "POST", json_payload=payload)

    # new endpoints (must match server routes)
    async def list_users(self):
        # server supports GET or POST; use GET
        return await self._request("/admin/list_users", "GET")

    async def change_user_password(self, target: str, new_password: str):
        payload = {"username": target, "new_password": new_password}
        return await self._request("/admin/change_user_password", "POST", json_payload=payload)

    async def change_user_username(self, old_username: str, new_username: str):
        payload = {"username": old_username, "new_username": new_username}
        return await self._request("/admin/change_user_username", "POST", json_payload=payload)

class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, tuple[str, callable]] = {}

    def register(self, name: str, desc: str, func: callable):
        self._commands[name] = (desc, func)

    def get(self, name: str):
        return self._commands.get(name)

    def all(self):
        return self._commands.items()

class AdminCLI:
    def __init__(self, client: HTTPXAdminClient):
        self.client = client
        self.registry = CommandRegistry()
        self._register_core_commands()

    def _register_core_commands(self):
        # base admin ops
        self.registry.register("list", "List users", self.cmd_list)
        self.registry.register("ban", "Ban a user", self.cmd_ban)
        self.registry.register("unban", "Unban a user", self.cmd_unban)
        self.registry.register("delete", "Delete a user", self.cmd_delete)
        self.registry.register("setpass", "Reset a user's password", self.cmd_setpass)
        self.registry.register("rename", "Rename a user", self.cmd_rename)
        self.registry.register("broadcast", "Broadcast message to all users", self.cmd_broadcast)
        self.registry.register("exit", "Exit CLI", self.cmd_exit)

    async def run(self):
        await self._login_flow()
        try:
            while True:
                self._print_header()
                try:
                    cmd = input(color("Choice (type 'help' for commands): ", C.CYAN)).strip()
                except KeyboardInterrupt:
                    print()
                    cmd = "exit"
                if not cmd:
                    continue
                if cmd == "help":
                    self._print_help()
                    continue
                parts = cmd.split()
                name = parts[0]
                args = parts[1:]
                entry = self.registry.get(name)
                if not entry:
                    prin("Unknown command, type 'help'", C.YELLOW)
                    continue
                func = entry[1]
                try:
                    await func(args)
                except APIError as e:
                    prin(f"API error: {e}", C.RED)
                    if isinstance(e.detail, dict):
                        try:
                            prin(json.dumps(e.detail, indent=2, ensure_ascii=False), C.RED)
                        except Exception:
                            pass
                except Exception as e:
                    prin(f"Error: {e}", C.RED)
        finally:
            await self.client.close()

    async def _login_flow(self):
        while True:
            prin("=== Admin login ===", C.HEADER)
            try:
                username = input(color("Username: ", C.BLUE)).strip()
            except KeyboardInterrupt:
                print()
                sys.exit(1)
            if not username:
                prin("Cancelled", C.YELLOW); sys.exit(1)
            pwd = getpass.getpass("Password: ")
            try:
                info = await self.client.login(username, pwd)
            except APIError as e:
                prin(f"Login failed: {e}", C.RED)
                continue
            role = info.get("role")
            if role != "admin":
                prin("Account is not admin (role != 'admin')", C.RED)
                continue
            prin(f"Logged in as {username}", C.GREEN)
            return

    def _print_header(self):
        prin("", C.END)
        prin(f"Admin: {self.client.username or 'NOT LOGGED IN'}", C.GREEN)
        prin("Commands:", C.CYAN)
        for k, (desc, _) in self.registry._commands.items():
            prin(f"  {k:12} - {desc}", C.BLUE)

    def _print_help(self):
        prin("Available commands:", C.CYAN)
        for name, (desc, _) in self.registry._commands.items():
            prin(f"  {name:12} - {desc}", C.BLUE)

    # ------------ new CLI commands ------------
    async def cmd_list(self, args: list[str]):
        """List users in a simple table."""
        try:
            resp = await self.client.list_users()
        except APIError as e:
            raise
        users = resp.get("users") or []
        if not users:
            prin("No users found", C.YELLOW)
            return
        # print table header
        prin(f"{'USERNAME':20} {'ROLE':8} {'CREATED (unix)'}", C.BOLD)
        for u in users:
            username = u.get("username", "")
            role = u.get("role", "")
            created = u.get("created", 0)
            try:
                created_h = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(created)))
            except Exception:
                created_h = str(created)
            prin(f"{username:20} {role:8} {created_h}", C.CYAN)

    async def cmd_setpass(self, args: list[str]):
        """Reset a user's password (admin)."""
        if not args:
            target = input(color("Username to reset password for: ", C.BLUE)).strip().lower()
        else:
            target = args[0].lower()
        if not target:
            prin("No username provided", C.YELLOW); return
        new_pw = getpass.getpass("New password: ")
        if not new_pw:
            prin("No password entered", C.YELLOW); return
        if len(new_pw) < 8:
            prin("Password too short (min 8)", C.YELLOW); return
        if not confirm(f"Confirm reset password for {target}?"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.change_user_password(target, new_pw)
        prin(f"Password reset for {target}", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    async def cmd_rename(self, args: list[str]):
        """Rename a user (admin)."""
        if len(args) >= 2:
            old = args[0].lower(); new = args[1].lower()
        else:
            old = input(color("Old username: ", C.BLUE)).strip().lower()
            new = input(color("New username: ", C.BLUE)).strip().lower()
        if not old or not new:
            prin("Missing old or new username", C.YELLOW); return
        if old == new:
            prin("Old and new username are the same", C.YELLOW); return
        if not confirm(f"Rename user {old} -> {new}?"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.change_user_username(old, new)
        prin(f"Renamed {old} -> {new}", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    # ------------ existing commands (improved with safer prints) ------------
    async def cmd_ban(self, args: list[str]):
        if not args:
            target = input(color("Username to ban: ", C.BLUE)).strip().lower()
        else:
            target = args[0].lower()
        if not target:
            prin("No username provided", C.YELLOW); return
        if target == self.client.username:
            prin("Cannot ban yourself", C.RED); return
        if not confirm(f"Confirm ban {target}?"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.ban_user(target)
        prin(f"Banned {target}", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    async def cmd_unban(self, args: list[str]):
        if not args:
            target = input(color("Username to unban: ", C.BLUE)).strip().lower()
        else:
            target = args[0].lower()
        if not target:
            prin("No username provided", C.YELLOW); return
        if not confirm(f"Confirm unban {target}?"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.unban_user(target)
        prin(f"Unbanned {target}", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    async def cmd_delete(self, args: list[str]):
        if not args:
            target = input(color("Username to delete: ", C.RED)).strip().lower()
        else:
            target = args[0].lower()
        if not target:
            prin("No username provided", C.YELLOW); return
        if target == self.client.username:
            prin("Cannot delete your own admin account here", C.RED); return
        if not confirm(f"*** PERMANENT DELETE {target}? This cannot be undone. Confirm"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.delete_user(target)
        prin(f"Deleted {target}", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    async def cmd_broadcast(self, args: list[str]):
        subj = ""
        if args:
            subj = args[0]
        if not subj:
            subj = input(color("Broadcast subject: ", C.BLUE)).strip()
        if not subj:
            prin("Missing subject", C.YELLOW); return
        prin("Enter message. End with a single '.' on a line.", C.YELLOW)
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
        if not msg:
            prin("Empty message", C.YELLOW); return
        prin("Preview:", C.CYAN)
        prin(f"Subject: {subj}", C.BLUE)
        for line in textwrap.wrap(msg, width=78):
            prin(line, C.CYAN)
        if not confirm("Send broadcast to ALL users?"):
            prin("Cancelled", C.YELLOW); return
        resp = await self.client.broadcast(subj, msg)
        prin("Broadcast sent", C.GREEN)
        prin(json.dumps(resp, indent=2, ensure_ascii=False), C.CYAN)

    async def cmd_exit(self, args: list[str]):
        prin("Bye", C.GREEN)
        await self.client.close()
        sys.exit(0)

async def main_async(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--server", "-s", default="http://omx.dedyn.io:30174")
    p.add_argument("--timeout", "-t", type=int, default=8)
    p.add_argument("--retries", "-r", type=int, default=4)
    p.add_argument("--backoff", "-b", type=float, default=0.4)
    args = p.parse_args(argv[1:])
    client = HTTPXAdminClient(base_url=args.server, timeout=args.timeout, retries=args.retries, backoff=args.backoff)
    cli = AdminCLI(client)
    try:
        await cli.run()
    except KeyboardInterrupt:
        prin("\nInterrupted", C.YELLOW)
    finally:
        await client.close()

def main():
    asyncio.run(main_async(sys.argv))

if __name__ == "__main__":
    main()