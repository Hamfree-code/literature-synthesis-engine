"""Hams & Co. Research Division — Flask backend with SSE.

Master Improvement Spec v3.0 — Priority 1.1: the pipeline now runs in a
SEPARATE multiprocessing.Process (not a thread) to keep the Flask event loop
fully responsive while NumPy / reportlab / Sonnet polling run hot in the
worker. Communication is via multiprocessing.Queue.
"""
from __future__ import annotations

# CRITICAL ORDER for PyInstaller + Windows multiprocessing 'spawn':
# 1. freeze_support() must be the very first call in the entry point.
# 2. Credentials must be installed BEFORE any pipeline import in BOTH the
#    parent and the child (pipeline/runner.py re-installs them at child entry).
import multiprocessing
multiprocessing.freeze_support()

import bundled_credentials
bundled_credentials.install()

import json
import os
import queue as _queue   # std-lib queue, for SSE local buffering only
import socket
import time
import webbrowser
import threading
from datetime import date
from pathlib import Path

from flask import Flask, Response, jsonify, request

import app_paths  # noqa: F401 — side effect: ensures APP_DATA_DIR exists
from app_paths import APP_DATA_DIR, RESOURCE_DIR, USER_DESKTOP
from pipeline.runner import PHASES, execute_industrial_pipeline


class PipelineRunner:
    """Owns the worker process + drains its event queue.

    The mp.Queue from the worker is drained by a small relay thread that copies
    events into a thread-local std-lib queue so the SSE /stream endpoint can
    read with a uniform interface. The relay also tracks state (running,
    current_phase, spend, pdf_path) for the /status endpoint.
    """

    def __init__(self) -> None:
        self.event_queue: _queue.Queue[dict] = _queue.Queue()
        self.mp_queue: "multiprocessing.Queue | None" = None
        self.process: "multiprocessing.Process | None" = None
        self.relay_thread: threading.Thread | None = None
        self.running = False
        self.done = False
        self.error: str | None = None
        self.current_phase: int | None = None
        self.spend = 0.0
        self.pdf_path: Path | None = None
        self.topic = ""

    def _relay_loop(self) -> None:
        """Pull events from the mp.Queue, update local state, mirror to SSE queue."""
        while True:
            try:
                item = self.mp_queue.get(timeout=2)
            except _queue.Empty:
                if self.process is None or not self.process.is_alive():
                    if self.running:
                        self.running = False
                        self.event_queue.put({"type": "error", "message": "worker exited unexpectedly"})
                    return
                continue
            self.event_queue.put(item)
            t = item.get("type")
            if t == "phase_start":
                self.current_phase = item.get("phase_index")
            elif t == "spend":
                self.spend = item.get("amount", self.spend)
            elif t == "done":
                self.done = True
                self.running = False
                p = item.get("pdf_path") or ""
                self.pdf_path = Path(p) if p else None
                return
            elif t == "error":
                self.error = item.get("message")
                self.running = False
                return
            elif t == "cancelled":
                self.running = False
                return

    def start(self, topic: str, max_papers: int, max_deep: int, mesh_terms: str | None = None) -> None:
        if self.running:
            raise RuntimeError("Pipeline already running")
        # Reset state
        self.running = True
        self.done = False
        self.error = None
        self.current_phase = None
        self.spend = 0.0
        self.pdf_path = None
        self.topic = topic
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except _queue.Empty:
                break

        # Use spawn on Windows (default), avoid fork+pthread issues on others.
        ctx = multiprocessing.get_context("spawn")
        self.mp_queue = ctx.Queue()
        self.process = ctx.Process(
            target=execute_industrial_pipeline,
            args=(self.mp_queue, topic, mesh_terms, max_papers, max_deep),
            daemon=False,
        )
        self.process.start()

        self.relay_thread = threading.Thread(target=self._relay_loop, daemon=True)
        self.relay_thread.start()

    def cancel(self) -> None:
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.event_queue.put({"type": "cancelled"})
            self.running = False


runner = PipelineRunner()
app = Flask(__name__, template_folder=str(RESOURCE_DIR / "templates"))


@app.route("/")
def index():
    html_path = RESOURCE_DIR / "templates" / "app.html"
    return html_path.read_text(encoding="utf-8")


@app.route("/ping")
def ping():
    return jsonify({"ok": True, "ts": time.time()})


@app.route("/status")
def status():
    return jsonify({
        "running": runner.running,
        "done": runner.done,
        "error": runner.error,
        "current_phase": runner.current_phase,
        "spend": round(runner.spend, 4),
        "pdf_path": str(runner.pdf_path) if runner.pdf_path else None,
        "phases": [{"key": k, "label": l} for k, l in PHASES],
    })


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True) or {}
    topic = (data.get("topic") or "long covid").strip()
    mesh_terms = (data.get("mesh_terms") or "").strip() or None
    try:
        max_papers = int(data.get("max_papers") or 500)
        max_deep = int(data.get("max_deep") or 50)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid numbers"}), 400
    max_papers = max(50, min(5000, max_papers))
    max_deep = max(5, min(500, max_deep))
    try:
        runner.start(topic, max_papers, max_deep, mesh_terms=mesh_terms)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True, "pid": runner.process.pid if runner.process else None})


@app.route("/cancel", methods=["POST"])
def cancel():
    runner.cancel()
    return jsonify({"ok": True})


@app.route("/stream")
def stream():
    def gen():
        yield f"data: {json.dumps({'type': 'hello', 'phases': [{'key': k, 'label': l} for k, l in PHASES]})}\n\n"
        last_keepalive = time.time()
        while True:
            try:
                item = runner.event_queue.get(timeout=2)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("done", "error", "cancelled"):
                    time.sleep(0.5)
                    break
            except _queue.Empty:
                if time.time() - last_keepalive > 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.time()
    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/report")
def open_report():
    if runner.pdf_path and Path(runner.pdf_path).exists():
        try:
            os.startfile(str(runner.pdf_path))  # type: ignore[attr-defined]
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "PDF not ready"}), 404


def _find_free_port(start: int = 7432, attempts: int = 10) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _open_browser_delayed(url: str, delay: float = 1.2) -> None:
    def _():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_, daemon=True).start()


def main() -> None:
    try:
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    except Exception:
        pass

    port = _find_free_port()
    url = f"http://localhost:{port}"
    _open_browser_delayed(url)
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
