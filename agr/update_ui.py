"""Desktop update state: metadata checks, readable notes and explicit install."""
from __future__ import annotations

import queue
import re
import threading
import webbrowser

from . import __version__, updater


def readable_notes(body):
    text = re.sub(r"(?m)^#{1,6}\s+", "", body)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    return text.replace("**", "").replace("*Before:*", "Before:").replace("*After:*", "After:").replace("`", "")


class UpdateUI:
    def __init__(self, app, root):
        self.app, self.root = app, root
        self.enabled = updater.installation_enabled(root)
        self.events = queue.Queue()
        self.running = False
        self.restarting = False
        self.available = None
        self.ready = None
        self.closed = False
        self.request = 0
        self._status("Check for new RenderLock releases." if self.enabled else
                     "Update installation is available in the Windows portable build.")
        app.root.after(150, self.poll)
        if self.enabled and app.values["check_updates"].get() == "1":
            app.root.after(2000, self.check)

    def _status(self, text):
        self.app.update_status.set(text)
        self.app.update_check.configure(state="disabled" if self.running or self.restarting else "normal")
        self.app.update_action.configure(text="Install and restart" if self.ready else "Download update",
            state="normal" if self.enabled and self.available and not self.running and not self.restarting else "disabled")
        self.app.update_notes_link.configure(state="normal" if self.available else "disabled")

    def _run(self, function, kind):
        self.running = True
        self.request += 1
        request = self.request

        def run():
            try:
                self.events.put((request, kind, function()))
            except Exception as error:
                self.events.put((request, "error", str(error)))
        threading.Thread(target=run, name="RenderLock update", daemon=True).start()

    def check(self):
        if self.closed or self.running or self.restarting:
            return
        if not self.enabled:
            self._status("Use the packaged Windows build for in-app updates.")
            return
        self._run(lambda: updater.check_latest(__version__), "checked")
        self._status("Checking published RenderLock releases...")

    def action(self):
        if self.closed or self.running or self.restarting or not self.available:
            return
        if self.app.busy or self.app.root.grab_current() is not None:
            self._status("Finish the current job or dialog, then click the update button again.")
            return
        if self.ready:
            self.install()
            return
        approved = dict(self.available)
        request = self.request + 1
        self._run(lambda: updater.download_update(approved, self.root,
            lambda message: self.events.put((request, "status", message))), "downloaded")
        self._status(f"Downloading RenderLock {approved['version']}...")

    def install(self):
        from .update_worker import launch_worker
        if self.app.busy or self.closed or not self.ready or self.running:
            return
        try:
            # Save per-user settings before handing control to an independent
            # runtime. Replacing application files never touches the user cache.
            self.app._save_settings()
            launch_worker(self.root, self.ready)
        except (OSError, ValueError, RuntimeError) as error:
            self._status("Could not start updater: " + str(error))
            return
        self.restarting = True
        self.closed = True
        self.app.root.destroy()

    def open_notes(self):
        if self.available:
            webbrowser.open(self.available["notes_url"])

    def poll(self):
        if self.closed:
            return
        try:
            for _ in range(100):
                request, kind, value = self.events.get_nowait()
                if request != self.request:
                    continue
                if kind == "status":
                    self._status(value)
                    continue
                self.running = False
                if kind == "checked":
                    self.available, self.ready = value, None
                    self.app.update_notes.configure(state="normal")
                    self.app.update_notes.delete("1.0", "end")
                    self.app.update_notes.insert("1.0", readable_notes(value["notes"]) if value else
                        "You have the newest published version available to this release channel.")
                    self.app.update_notes.configure(state="disabled")
                    self.app.rail.itemconfigure(self.app.nav_items["Updates"]["text"],
                                                text="Updates (new)" if value else "Updates")
                    self._status(f"RenderLock {value['version']} is available. Review the notes below." if value else
                                 f"RenderLock {__version__} is up to date.")
                elif kind == "downloaded":
                    self.ready = value
                    self._status("Update verified and ready. Click Install and restart when your work is finished.")
                else:
                    self._status("Update deferred: " + value + ". Your installed version is unchanged.")
        except queue.Empty:
            pass
        self.app.root.after(150, self.poll)
