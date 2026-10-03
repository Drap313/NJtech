"""Push new incidents into Slack through the OpenClaw agent in the NemoClaw sandbox.

When the monitor opens or materially updates an incident, the dispatcher
notification lands in the outbox. This notifier picks it up and runs one OpenClaw
agent turn in the Slack channel's own session, so the agent posts the incident
with its Approve/Reject buttons and any clicks come back to that same session.

Enabled unless DG_OPENCLAW_NOTIFY=0. Settings:
  DG_OPENCLAW_SANDBOX   (default dispatch-guardian)
  DG_SLACK_CHANNEL_ID   (default C0C627V9S4F)
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import threading

log = logging.getLogger("dispatch.openclaw_notify")

NODE_BIN = os.path.expanduser("~/.nvm/versions/node/v22.23.3/bin")
LOCAL_BIN = os.path.expanduser("~/.local/bin")


class OpenClawNotifier:
    def __init__(self, ctx):
        self.ctx = ctx
        self.sandbox = os.environ.get("DG_OPENCLAW_SANDBOX", "dispatch-guardian")
        self.channel = os.environ.get("DG_SLACK_CHANNEL_ID", "C0C627V9S4F")
        self.session_key = f"agent:main:slack:channel:{self.channel.lower()}"
        self.env = {**os.environ, "PATH": f"{NODE_BIN}:{LOCAL_BIN}:{os.environ.get('PATH', '')}", "NEMOCLAW_NO_POLICY_HINT": "1"}
        self.enabled = os.environ.get("DG_OPENCLAW_NOTIFY", "1") != "0" and bool(shutil.which("nemoclaw", path=self.env["PATH"]))
        self.status = {"enabled": self.enabled, "sent": 0, "last": None, "last_error": None, "busy": False}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.enabled:
            threading.Thread(target=self._loop, name="openclaw-notify", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(3):
            try:
                self.flush()
            except Exception as e:  # keep the monitor alive
                log.exception("openclaw notify failed")
                self.status["last_error"] = repr(e)

    def flush(self) -> int:
        store = self.ctx.store
        rows = [r for r in reversed(store.outbox_list(50, "queued")) if r["channel"] == "dispatcher" and r["incident_id"]]
        sent = 0
        for r in rows:
            inc = store.get_incident(r["incident_id"])
            store.outbox_set_status(r["id"], "sent")  # claim it first so nothing is posted twice
            if not inc or inc["status"] != "OPEN":
                continue
            self.notify(inc)
            sent += 1
        return sent

    def message_for(self, inc: dict) -> str:
        if inc.get("plans"):
            return (f"[Dispatch Guardian alert] Incident {inc['id']} ({inc['severity']}) just opened: {inc['title']} "
                    f"Run `dg.sh incident {inc['id']}` and post it to this channel now for the dispatchers: what broke, the options "
                    f"with arrival, spare minutes, legal time left and extra cost, and end with the [[slack_buttons: ...]] line "
                    f"copied exactly from the output. Do not approve anything.")
        return (f"[Dispatch Guardian alert] Incident {inc['id']} ({inc['severity']}) opened: {inc['title']} Cause: {inc['cause']}. "
                f"Post a short heads-up to this channel. No decision is needed yet.")

    def notify(self, inc: dict) -> None:
        msg = self.message_for(inc)
        cmd = (f"nemoclaw {shlex.quote(self.sandbox)} agent --agent main --session-key {shlex.quote(self.session_key)} "
               f"-m {shlex.quote(msg)} --deliver --reply-channel slack --reply-to {shlex.quote('channel:' + self.channel)}")
        with self._lock:
            self.status["busy"] = True
            try:
                out = subprocess.run(["sg", "docker", "-c", cmd], env=self.env, capture_output=True, text=True, timeout=300)
                ok = out.returncode == 0
                self.status.update(last={"incident": inc["id"], "ok": ok, "rc": out.returncode},
                                   last_error=None if ok else (out.stderr or out.stdout)[-500:])
                if ok:
                    self.status["sent"] += 1
                self.ctx.store.audit("slack_alert_via_openclaw", f"incident:{inc['id']}", {"ok": ok, "rc": out.returncode,
                                     "session": self.session_key}, self.ctx.clock.now().isoformat())
            finally:
                self.status["busy"] = False
