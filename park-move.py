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


def repo_write(state, mover="agent"):
    body = json.dumps(state, indent=2).encode()
    for attempt in range(2):
        sha, _current = repo_read()
        status, out = api(
            "PUT",
            f"/repos/{OWNER}/{REPO}/contents/{PATH}",
            {
                "message": f"dog-park: {mover} move",
                "content": base64.b64encode(body).decode(),
                "sha": sha,
            },
        )
        if status in (200, 201):
            return out
        # 409/422 = sha race; retry once with a fresh read
    raise RuntimeError(f"write failed: {status} {out}")


def local_read(path):
    with open(path) as f:
        return None, json.load(f)


def local_write(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    return {"path": path}


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
            _sha, state = local_read(local) if local else repo_read()
            b = state.get("bounds", {})
            if not (b.get("min_x", -15) <= x <= b.get("max_x", 15)
                    and b.get("min_z", -15) <= z <= b.get("max_z", 15)):
                print(f"position ({x}, {z}) is outside bounds {b}")
                return 2
            entry = state["agents"][agent]
            entry["x"] = x
            entry["z"] = z
            if args["facing"] is not None:
                entry["facing"] = args["facing"] % 360
            if args["action"]:
                entry["action"] = args["action"]
            if args["status"] is not None:
                entry["status"] = args["status"][:140]
            state["updated"] = now_iso()
            if local:
                local_write(local, state)
            else:
                repo_write(state, mover=agent)
            print(json.dumps({"ok": True, agent: entry, "updated": state["updated"]}))
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
