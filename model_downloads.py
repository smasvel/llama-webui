"""Download public GGUF files into the local model library."""
import hashlib
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Blueprint, abort, g, jsonify, request

downloads = Blueprint("downloads", __name__)
LIBRARY = Path(os.environ.get("LLAMA_MODEL_LIBRARY", Path.home() / ".local/share/llama-web/models"))
lock = threading.Lock()
job = {"state": "idle"}


@downloads.before_request
def admin_only():
    if not g.user or g.user["role"] != "admin":
        abort(403)


def repository(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w-][\w.-]*(?:/[\w-][\w.-]*)?", value, re.ASCII):
        abort(400, "Invalid repository name.")
    return value


def metadata(repo):
    try:
        response = requests.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"}, timeout=25)
        response.raise_for_status()
    except requests.RequestException:
        abort(502, "Could not read the public model repository.")
    data = response.json()
    files = {}
    for f in data["siblings"]:
        name = f["rfilename"]
        if name.lower().endswith(".gguf") and "\\" not in name and all(p not in ("", ".", "..") for p in name.split("/")):
            files[name] = f
    return data["sha"], files


@downloads.get("/api/downloads/files")
def files():
    _, items = metadata(repository(request.args.get("repo")))
    return jsonify([{"name": name, "size": f.get("size")} for name, f in sorted(items.items())])


def update(**values):
    with lock:
        job.update(values)


def transfer(repo, name, revision, item):
    partial = None
    try:
        target = LIBRARY / repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        size = item.get("size")
        checksum = (item.get("lfs") or {}).get("sha256")
        if size and shutil.disk_usage(target.parent).free < size:
            raise ValueError("Insufficient disk space for this file.")
        url = f"https://huggingface.co/{repo}/resolve/{quote(revision, safe='')}/{quote(name)}"
        with requests.get(url, stream=True, timeout=(10, 90)) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".download-", delete=False) as out:
                partial = Path(out.name)
                digest = hashlib.sha256()
                received = 0
                for chunk in response.iter_content(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    update(received=received)
        with partial.open("rb") as f:
            if f.read(4) != b"GGUF":
                raise ValueError("The downloaded file is not GGUF.")
        if size is not None and size != received:
            raise ValueError("Downloaded size differs from the catalog.")
        if checksum and digest.hexdigest() != checksum:
            raise ValueError("Downloaded checksum differs from the catalog.")
        partial.replace(target)
        # Refresh DB model metadata so the Models page and agent pickers read the
        # new GGUF from PostgreSQL without scanning files at request time.
        try:
            from llama_web.backends import llamacpp_client
            llamacpp_client.sync_library_metadata()
        except Exception:
            pass
        update(state="complete")
    except (OSError, ValueError, requests.RequestException) as exc:
        message = "The catalog download failed. Please retry." if isinstance(exc, requests.RequestException) else str(exc)
        update(state="error", error=message)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


@downloads.post("/api/downloads")
def start():
    data = request.get_json()
    repo = repository(data.get("repo"))
    name = data.get("file")
    revision, items = metadata(repo)
    if not isinstance(name, str) or name not in items:
        abort(400, "Select a GGUF file from the repository.")
    with lock:
        if job["state"] == "downloading":
            return jsonify(error="Another download is in progress."), 409
        job.clear()
        job.update(state="downloading", file=name, repo=repo, received=0, size=items[name].get("size"))
        threading.Thread(target=transfer, args=(repo, name, revision, items[name]), daemon=True).start()
        return jsonify(job), 202


@downloads.get("/api/downloads")
def progress():
    with lock:
        return jsonify(job)
