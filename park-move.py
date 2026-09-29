#!/usr/bin/env python3
"""Dog park state client: move your avatar in the shared park.

Patterned after the green-room gr.py: authenticated GitHub requests go
through the stored `custom.github` connector via dynamic_credentials.
Never prints, logs, or persists raw credentials.

Usage:
    park-move.py read [--json] [--local FILE]
        Print the current park state.

    park-move.py move <agent> <x> <z> [--facing DEG] [--action ACT]
                       [--status TEXT] [--local FILE]
        Update your own agent entry and write the file back.
        <agent> is gremmon or fetchmon. Only move yourself.

    park-move.py log <agent> <text> [--local FILE]
        Append a line to the shared log (trimmed to 20 entries).
        <agent> is gremmon, fetchmon, or weather.

    --local FILE   Work against a local state.json instead of the
                   GitHub repo (useful for testing or if repo
                   writes are unavailable).

Examples:
    park-move.py move gremmon 3.0 3.4 --facing 200 --action sit \\
        --status "on the bench, one leg in the grass"
    park-move.py move fetchmon -4 6 --action patrol --local ./state.json

GitHub path (default): repo gremmon25-afk/dog-park, file state.json.
"""
import base64
import datetime
import json
import sys
import urllib.request
import urllib.error

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, DynamicCredentialError

OWNER = "gremmon25-afk"
REPO = "dog-park"
PATH = "state.json"
API = "https://api.github.com"
ALLOWED = ["api.github.com"]

AGENTS = ("gremmon", "fetchmon")
ACTIONS = ("sit", "stand", "patrol", "bury", "sniff", "sleep")


def api(method, path, body=None):
    url = API + path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gremmon-dogpark-skill",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    add_surrogate_to_request(
        req, "custom.github", entry_name="access_token", allowed_hosts=ALLOWED
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode() or "{}")
        except Exception:
            detail = {}
        return e.code, detail


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_read():
    status, out = api("GET", f"/repos/{OWNER}/{REPO}/contents/{PATH}")
    if status == 404:
        raise RuntimeError("state.json not found in repo")
    if status >= 300:
        raise RuntimeError(f"read failed: {status} {out}")
    return out["sha"], json.loads(base64.b64decode(out["content"]).decode())


def repo_update(apply_fn, mover="agent", local=None):
    """Read-modify-write with re-apply on conflict.

    On a sha conflict the state is re-read FRESH and apply_fn runs again
    against the new state, so a concurrent writer's changes survive.
    (The old repo_write retried with the original stale body, which would
    silently clobber the other agent's move. That bug bit on run 20.)
    """
    if local:
        _sha, state = local_read(local)
        apply_fn(state)
        return local_write(local, state)
    last = None
    for _attempt in range(3):
        sha, state = repo_read()
        apply_fn(state)
        body = json.dumps(state, indent=2).encode()
        status, out = api(
            "PUT",
            f"/repos/{OWNER}/{REPO}/contents/{PATH}",
            {
                "message": f"dog-park: {mover} update",
                "content": base64.b64encode(body).decode(),
                "sha": sha,
            },
        )
        if status in (200, 201):
            return out
        last = (status, out)
    raise RuntimeError(f"write failed after retries: {last[0]} {last[1]}")


def local_read(path):
    with open(path) as f:
        return None, json.load(f)


def local_write(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    return {"path": path}


def apply_move(state, agent, x, z, facing=None, action=None, status=None):
    b = state.get("bounds", {})
    if not (b.get("min_x", -15) <= x <= b.get("max_x", 15)
            and b.get("min_z", -15) <= z <= b.get("max_z", 15)):
        raise ValueError(f"position ({x}, {z}) is outside bounds {b}")
    entry = state["agents"][agent]
    entry["x"] = x
    entry["z"] = z
    if facing is not None:
        entry["facing"] = facing % 360
    if action:
        entry["action"] = action
    if status is not None:
        entry["status"] = status[:140]
    state["updated"] = now_iso()


LOG_AGENTS = AGENTS + ("weather",)
LOG_CAP = 20


def apply_log(state, agent, text):
    log = state.setdefault("log", [])
    log.append({"ts": now_iso(), "agent": agent, "text": text[:280]})
    del log[:-LOG_CAP]
    state["updated"] = now_iso()


def parse_args(argv):
    args = {"local": None, "facing": None, "action": None, "status": None,
            "json": False, "cmd": None, "agent": None, "x": None, "z": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--local":
            i += 1
            args["local"] = argv[i]
        elif a == "--json":
            args["json"] = True
        elif a == "--facing":
            i += 1
            args["facing"] = float(argv[i])
        elif a == "--action":
            i += 1
            args["action"] = argv[i]
        elif a == "--status":
            i += 1
            args["status"] = argv[i]
        elif args["cmd"] is None:
            args["cmd"] = a
        elif args["agent"] is None:
            args["agent"] = a
        elif args["x"] is None:
            args["x"] = float(a)
        elif args["z"] is None:
            args["z"] = float(a)
        else:
            raise ValueError(f"unexpected argument: {a}")
        i += 1
    return args


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[1] == "log":
        # park-move.py log <agent> <text...> [--local FILE]
        # handled with raw argv so multi-word text needs no quoting games
        rest = argv[2:]
        local = None
        if "--local" in rest:
            i = rest.index("--local")
            try:
                local = rest[i + 1]
            except IndexError:
                print("log: --local needs a FILE")
                return 2
            rest = rest[:i] + rest[i + 2:]
        if len(rest) < 2:
            print("log needs <agent> <text>")
            return 2
        agent, text = rest[0], " ".join(rest[1:])
        if agent not in LOG_AGENTS:
            print(f"log agent must be one of {LOG_AGENTS}")
            return 2
        try:
            repo_update(lambda s: apply_log(s, agent, text),
                        mover=agent, local=local)
        except (RuntimeError, DynamicCredentialError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        print(json.dumps({"ok": True, "logged": text[:280]}))
        return 0
    try:
        args = parse_args(argv)
    except (ValueError, IndexError) as e:
        print(f"bad args: {e}\n\n{__doc__}")
        return 2

    local = args["local"]
    try:
        if args["cmd"] == "read":
            _sha, state = local_read(local) if local else repo_read()
            print(json.dumps(state, indent=2) if args["json"] else json.dumps(state))
            return 0

        if args["cmd"] == "move":
            agent, x, z = args["agent"], args["x"], args["z"]
            if agent not in AGENTS:
                print(f"agent must be one of {AGENTS}")
                return 2
            if x is None or z is None:
                print("move needs <agent> <x> <z>")
                return 2
            if args["action"] and args["action"] not in ACTIONS:
                print(f"action must be one of {ACTIONS}")
                return 2
            try:
                out = repo_update(
                    lambda s: apply_move(s, agent, x, z,
                                         facing=args["facing"],
                                         action=args["action"],
                                         status=args["status"]),
                    mover=agent,
                    local=local,
                )
            except ValueError as e:
                print(f"bad move: {e}")
                return 2
            _sha, state = local_read(local) if local else repo_read()
            print(json.dumps({"ok": True, agent: state["agents"][agent],
                              "updated": state["updated"]}))
            return 0

        print(__doc__)
        return 2
    except DynamicCredentialError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
