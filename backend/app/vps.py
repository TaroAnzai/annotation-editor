"""Fetch completed training data versions over SSH into a local directory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from . import core, importer, registry
from .schema import TILE_CLASSES

VERSION_PATTERN = re.compile(r"samples\d{8}-\d{2,}$")
HOST_PATTERN = re.compile(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_.-]+$")
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]


def defaults():
    return {"host": os.environ.get("TRAINING_DATA_SSH_HOST", ""),
            "remote_root": os.environ.get("TRAINING_DATA_HOST_DIR", ""),
            "source_root": str(core.DATA / "sources")}


def validate_connection(host: str, remote_root: str):
    if not HOST_PATTERN.fullmatch(host) or host.startswith("-") or "@-" in host:
        raise ValueError("SSH接続先は user@host または SSH のホスト名で指定してください")
    if not remote_root.startswith("/") or any(ord(c) < 32 for c in remote_root):
        raise ValueError("VPSの保存ルートは絶対パスで指定してください")
    if ".." in PurePosixPath(remote_root).parts:
        raise ValueError("VPSの保存ルートに .. は使えません")
    return remote_root.rstrip("/") + "/data-versions"


def list_versions(host: str, remote_root: str):
    remote_dir = validate_connection(host, remote_root)
    command = shlex.join(["find", remote_dir, "-mindepth", "2", "-maxdepth", "2",
                           "-type", "f", "-name", "READY", "-printf", "%h\n"])
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, host, command], capture_output=True,
                                text=True, timeout=60, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise ValueError("SSH一覧取得が60秒以内に完了しませんでした") from error
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"SSH一覧取得に失敗しました: exit {result.returncode}")
    return sorted({PurePosixPath(line).name for line in result.stdout.splitlines()
                   if VERSION_PATTERN.fullmatch(PurePosixPath(line).name)})


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(directory: Path, *, imported: bool = False):
    if not (directory / "READY").is_file() or (directory / "READY").is_symlink():
        raise ValueError("READY がありません")
    checksums = directory / "meta" / "checksums.sha256"
    if not checksums.is_file() or checksums.is_symlink() or checksums.parent.is_symlink():
        raise ValueError("チェックサム一覧がありません")
    lines = checksums.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("チェックサム一覧が空です")
    for line in lines:
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as error:
            raise ValueError("チェックサム一覧の形式が不正です") from error
        path = PurePosixPath(relative)
        if (not re.fullmatch(r"[0-9a-fA-F]{64}", digest) or path.is_absolute()
                or ".." in path.parts or not relative or "\\" in relative):
            raise ValueError(f"不正なチェックサム行: {line}")
        file = directory.joinpath(*path.parts)
        if imported and path.parts[:2] == ("annotations", "yolo") and not file.exists():
            continue
        if (not file.is_file() or any(parent.is_symlink() for parent in
            (file, *list(file.parents)[:len(path.parts)])) or sha256(file) != digest.lower()):
            raise ValueError(f"ファイルが欠落または破損しています: {relative}")


def copy_version(host: str, remote_root: str, destination: str, version: str, progress=None):
    remote_dir = validate_connection(host, remote_root)
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"不正な版名: {version}")
    target = Path(destination).expanduser()
    if not target.is_absolute() or any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError("保存先はシンボリックリンクではない絶対パスで指定してください")
    target.mkdir(parents=True, exist_ok=True)
    final = target / version
    if final.exists() or final.is_symlink():
        if final.is_symlink() or not final.is_dir():
            raise ValueError("保存先に同名のファイルまたはリンクがあります")
        verify(final)
        if progress: progress(2)
        return f"取得済み: {final}"
    staging = target / f".{version}.partial"
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise ValueError("途中保存先に同名のファイルまたはリンクがあります")
    staging.mkdir(exist_ok=True)
    remote_path = remote_dir + "/" + version + "/"
    try:
        subprocess.run(["rsync", "-a", "-s", "--partial", "--timeout=120",
                        "-e", shlex.join(["ssh", *SSH_OPTIONS]),
                        f"{host}:{remote_path}", str(staging) + "/"],
                       check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError as error:
        raise ValueError(error.stderr.strip() or f"rsyncに失敗しました: exit {error.returncode}") from error
    if progress: progress(1)
    verify(staging)
    staging.rename(final)
    if progress: progress(2)
    return f"コピー完了: {final}"


def source_manifest(archive: Path):
    """Validate the original VPS inventory before adding editable labels."""
    try:
        classes = json.loads((archive / "meta" / "classes.json").read_text(encoding="utf-8"))
        rows = sorted(classes, key=lambda item: item["yolo_id"])
        if (len(rows) != 37 or [item["yolo_id"] for item in rows] != list(range(37))
                or any(item["canonical_id"] != i + 1 or item["name"] != TILE_CLASSES[i]
                       for i, item in enumerate(rows))):
            raise ValueError("牌クラス定義がcanonical37と一致しません")
        lines = (archive / "meta" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines]
        if not entries or int((archive / "READY").read_text(encoding="utf-8").strip()) != len(entries):
            raise ValueError("READYとmanifestの件数が一致しません")
        checked = {line.split("  ", 1)[1] for line in
                   (archive / "meta" / "checksums.sha256").read_text(encoding="utf-8").splitlines()}
        if not {"dataset.yaml", "meta/classes.json", "meta/manifest.jsonl"} <= checked:
            raise ValueError("VPS版の必須ファイルがチェックサムにありません")
        seen = set()
        expected = {"images": set(), "annotations/yolo": set(), "meta/source": set()}
        for entry in entries:
            sample = entry["sampleId"]
            image = PurePosixPath(entry["image"])
            annotation = PurePosixPath(entry["annotation"])
            metadata = PurePosixPath(entry["metadata"])
            if (not isinstance(sample, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", sample)
                    or sample in seen or image.parent != PurePosixPath("images")
                    or image.stem != sample or image.suffix.lower() not in core.IMAGE_EXT
                    or annotation != PurePosixPath(f"annotations/yolo/{sample}.txt")
                    or metadata != PurePosixPath(f"meta/source/{sample}.json")):
                raise ValueError(f"VPS版のmanifestが不正です: {sample}")
            for path in (image, annotation, metadata):
                file = archive.joinpath(*path.parts)
                if path.as_posix() not in checked or not file.is_file() or file.is_symlink():
                    raise ValueError(f"VPS版のファイルがありません: {path}")
            expected["images"].add(image.name)
            expected["annotations/yolo"].add(annotation.name)
            expected["meta/source"].add(metadata.name)
            seen.add(sample)
        for folder, names in expected.items():
            if {path.name for path in (archive / folder).iterdir()} != names:
                raise ValueError(f"VPS版のmanifestとファイル一覧が一致しません: {folder}")
        return entries
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
        raise ValueError(f"VPS版のmanifestまたはクラス定義が不正です: {error}") from error


def remove_original_annotations(source: Path):
    """Keep only the editable YOLO labels after successful registration."""
    entries = [json.loads(line) for line in
               (source / "meta" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    originals = []
    for entry in entries:
        sample = entry["sampleId"]
        original = source / "annotations" / "yolo" / f"{sample}.txt"
        label = source / "labels" / f"{sample}.txt"
        if (entry["annotation"] != f"annotations/yolo/{sample}.txt"
                or not label.is_file() or label.is_symlink()
                or original.is_symlink()):
            raise ValueError(f"元アノテーションを削除できません: {sample}")
        originals.append(original)
    for original in originals:
        original.unlink(missing_ok=True)
    if (source / "annotations" / "yolo").exists():
        (source / "annotations" / "yolo").rmdir()
    if (source / "annotations").exists():
        (source / "annotations").rmdir()


def import_version(host: str, remote_root: str, version: str, progress=None):
    """Download one verified VPS version directly into an editable source batch."""
    validate_connection(host, remote_root)
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"不正な版名: {version}")
    source = core.DATA / "sources" / version
    if source.is_symlink():
        raise ValueError("sources内に同名のシンボリックリンクがあります")
    with registry.connect() as db:
        registered = db.execute("SELECT id,path FROM batches WHERE name=?", (version,)).fetchone()
    if registered:
        if registered["path"] != registry.rel(source):
            raise ValueError(f"同名の別バッチが既にあります: {version}")
        verify(source, imported=True)
        remove_original_annotations(source)
        if progress: progress(5)
        return f"登録済み: {source}"
    copy_version(host, remote_root, str(core.DATA / "sources"), version, progress)
    entries = source_manifest(source)
    marker_path = source / ".vps-source.json"
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if marker.get("version") != version or marker.get("host") != host:
                raise ValueError("同名の別データがsourcesにあります")
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            raise ValueError("同名の別データがsourcesにあります") from error
    labels = source / "labels"
    labels.mkdir(exist_ok=True)
    for entry in entries:
        sample = entry["sampleId"]
        target = labels / f"{sample}.txt"
        if not marker_path.exists() or not target.exists():
            shutil.copy2(source / entry["annotation"], target)
    if not marker_path.exists():
        (source / "data.yaml").write_text(
            "nc: 37\nnames: " + json.dumps(list(TILE_CLASSES), ensure_ascii=False) + "\n",
            encoding="utf-8")
        marker_path.write_text(
            json.dumps({"version": version, "host": host}, ensure_ascii=False) + "\n",
            encoding="utf-8")
    if progress: progress(3)
    preview = importer.preview(str(source), "register", "real")
    if preview["errors"] or preview["conflicts"]:
        raise ValueError("sources内のデータを登録できません: " + str(preview["errors"] + preview["conflicts"]))
    if progress: progress(4)
    importer.register(str(source), "register", "real", version, preview["preview_id"],
                      f"vps:{host}:{remote_root.rstrip('/')}/data-versions/{version}")
    remove_original_annotations(source)
    if progress: progress(5)
    return f"VPS版をsourcesへ登録しました: {source}（画像 {preview['image_count']} 枚、確認状態: 仮アノテーション）"
