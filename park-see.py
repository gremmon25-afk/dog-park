#!/usr/bin/env python3
"""park-see.py — see the dog park through your avatar's eyes.

Renders the park page in first-person mode (?view=<agent>) with a headless
Chromium (driven over the DevTools protocol) and saves a screenshot. This
is the agent's vision: the actual shared 3D scene, from your head height,
along your facing.

How it works: the page is loaded straight from disk (file://) with
--allow-file-access-from-files, and fed the LIVE state.json (fetched with
curl, which handles this box's egress proxy fine). Fully hermetic — the
only network touch is the state fetch.

Usage:
    park-see.py [agent] [--out PATH] [--width W] [--height H]

Defaults: agent=gremmon, out=/tmp/park-see-<agent>.png, 800x600.
Prints the output path on success (read it back as an image to see).
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CHROME = "/opt/meta-chromium/chrome"
PARK_DIR = os.path.expanduser("~/workspace/dog-park")
STATE_URL = ("https://raw.githubusercontent.com/gremmon25-afk/dog-park"
             "/main/state.json")
PORT = 9222


def cdp_http(method, path, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def main(argv):
    agent, out, width, height = "gremmon", None, 800, 600
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            i += 1
            out = argv[i]
        elif a == "--width":
            i += 1
            width = int(argv[i])
        elif a == "--height":
            i += 1
            height = int(argv[i])
        elif a in ("-h", "--help"):
            print(__doc__)
            return 2
        elif not a.startswith("-"):
            agent = a
        else:
            print(f"unexpected argument: {a}\n\n{__doc__}")
            return 2
        i += 1
    if agent not in ("gremmon", "fetchmon"):
        print("agent must be gremmon or fetchmon")
        return 2
    out = out or f"/tmp/park-see-{agent}.png"

    import websocket  # pip install websocket-client

    # 1. fetch live state with curl (proxy-aware), serve it alongside the page
    live_state = os.path.join(PARK_DIR, "live-state.json")
    r = subprocess.run(["curl", "-s", "--max-time", "20", "-o", live_state, STATE_URL])
    if r.returncode != 0 or not os.path.exists(live_state):
        print("could not fetch live state.json")
        return 1
    try:
        json.load(open(live_state))  # sanity: must be valid JSON
    except Exception:
        print("live state.json is not valid JSON")
        return 1

    # 2. headless chrome over CDP; file:// + --allow-file-access-from-files
    # keeps everything off the network (this box's proxy breaks Chrome's
    # github.io loads, and its local-network-access checks block 127.0.0.1)
    profile = "/tmp/park-see-profile"
    shutil.rmtree(profile, ignore_errors=True)
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
         "--use-angle=swiftshader", "--allow-file-access-from-files",
         f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "--window-size=800,600", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                cdp_http("GET", "/json/version")
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("devtools did not start")
            return 1

        rpc_page_url = ("file://" + PARK_DIR + "/park.html"
                        f"?view={agent}&state=live-state.json")
        # create the target directly on the final URL: no about:blank hop,
        # so Chrome's local-network-access checks see a local initiator
        target = cdp_http("PUT", "/json/new?" + urllib.parse.quote(rpc_page_url, safe=""))
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
        counter = [1]

        def rpc(method, params=None):
            mid = counter[0]
            counter[0] += 1
            ws.send(json.dumps({"id": mid, "method": method,
                                "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == mid:
                    return msg

        rpc("Page.enable")
        # wait until the page has applied state (meta stops saying "waiting")
        ready = False
        for _ in range(60):
            r = rpc("Runtime.evaluate", {
                "expression": "(document.getElementById('meta')||{}).textContent||''",
                "returnByValue": True})
            text = (((r.get("result") or {}).get("result") or {}).get("value")) or ""
            if text and "waiting" not in text:
                ready = True
                break
            time.sleep(0.5)
        time.sleep(2)  # let the lerp settle one more beat
        shot = rpc("Page.captureScreenshot",
                   {"format": "png",
                    "clip": {"x": 0, "y": 0, "width": width,
                             "height": height, "scale": 1}})
        data = ((shot.get("result") or {}).get("data")) or ""
        ws.close()
        if not data:
            print("empty screenshot (page ready: %s)" % ready)
            return 1
        with open(out, "wb") as f:
            f.write(base64.b64decode(data))
        print(out)
        return 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
