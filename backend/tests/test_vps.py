import hashlib
import json
import subprocess

import pytest
from PIL import Image

from app import core, registry, vps
from app.schema import TILE_CLASSES


def test_lists_only_ready_training_versions(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "/srv/data-versions/samples20261017-01\n/srv/data-versions/other\n/srv/data-versions/samples20261017-02\n", "")

    monkeypatch.setattr(vps.subprocess, "run", run)
    assert vps.list_versions("user@example.com", "/srv") == ["samples20261017-01", "samples20261017-02"]
    assert calls[0][0][0] == "ssh"
    assert "BatchMode=yes" in calls[0][0]
    assert calls[0][1]["timeout"] == 60


def test_copy_verifies_and_keeps_partial_on_failure(tmp_path, monkeypatch):
    version = "samples20261017-01"
    destination = tmp_path / "training-versions"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        staging = destination / f".{version}.partial"
        (staging / "meta").mkdir()
        (staging / "READY").touch()
        (staging / "data.bin").write_bytes(b"training data")
        digest = hashlib.sha256(b"training data").hexdigest()
        (staging / "meta" / "checksums.sha256").write_text(f"{digest}  data.bin\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(vps.subprocess, "run", run)
    steps = []
    assert vps.copy_version("vps", "/srv", str(destination), version, steps.append).startswith("コピー完了:")
    assert steps == [1, 2]
    assert calls[0][0] == "rsync"
    assert (destination / version / "data.bin").read_bytes() == b"training data"
    assert vps.copy_version("vps", "/srv", str(destination), version).startswith("取得済み:")
    assert len(calls) == 1
    (destination / version / "data.bin").write_bytes(b"broken")
    with pytest.raises(ValueError, match="破損"):
        vps.copy_version("vps", "/srv", str(destination), version)


def test_rejects_invalid_paths_and_checksum_escape(tmp_path):
    with pytest.raises(ValueError):
        vps.list_versions("-bad", "/srv")
    with pytest.raises(ValueError):
        vps.list_versions("vps", "relative")
    with pytest.raises(ValueError):
        vps.copy_version("vps", "/srv", str(tmp_path), "../other")
    directory = tmp_path / "version"
    (directory / "meta").mkdir(parents=True)
    (directory / "READY").touch()
    (directory / "meta" / "checksums.sha256").write_text("0" * 64 + "  ../outside\n")
    with pytest.raises(ValueError, match="不正"):
        vps.verify(directory)


def test_failed_verification_keeps_partial_for_retry(tmp_path, monkeypatch):
    destination = tmp_path / "copies"
    version = "samples20261017-03"

    def run(args, **kwargs):
        staging = destination / f".{version}.partial"
        (staging / "meta").mkdir()
        (staging / "READY").touch()
        (staging / "meta" / "checksums.sha256").write_text("0" * 64 + "  missing.bin\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(vps.subprocess, "run", run)
    with pytest.raises(ValueError, match="欠落"):
        vps.copy_version("vps", "/srv", str(destination), version)
    assert (destination / f".{version}.partial" / "READY").exists()
    assert not (destination / version).exists()


def test_imports_vps_version_as_editable_provisional_source(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path / "data")
    version = "samples20261017-04"
    source_root = core.DATA / "sources"
    archive = source_root / version
    sample = "00000000-0000-4000-8000-000000000001"
    for folder in ("images", "annotations/yolo", "meta/source"):
        (archive / folder).mkdir(parents=True)
    Image.new("RGB", (40, 30), (30, 40, 50)).save(archive / "images" / f"{sample}.jpg")
    (archive / "annotations" / "yolo" / f"{sample}.txt").write_text("0 0.5 0.5 0.4 0.4\n")
    (archive / "meta" / "source" / f"{sample}.json").write_text("{}")
    (archive / "meta" / "classes.json").write_text(json.dumps([
        {"canonical_id": i + 1, "name": {'red5m':'5mr','red5p':'5pr','red5s':'5sr'}.get(name,name), "yolo_id": i}
        for i, name in enumerate(TILE_CLASSES)
    ]))
    (archive / "dataset.yaml").write_text(f"name: {version}\nformat: yolo\nclass_schema: canonical37\n")
    (archive / "meta" / "manifest.jsonl").write_text(json.dumps({
        "sampleId": sample, "image": f"images/{sample}.jpg",
        "annotation": f"annotations/yolo/{sample}.txt",
        "metadata": f"meta/source/{sample}.json",
    }) + "\n")
    checksums = [f"{vps.sha256(p)}  {p.relative_to(archive).as_posix()}\n"
                 for p in sorted(archive.rglob("*")) if p.is_file()]
    (archive / "meta" / "checksums.sha256").write_text("".join(checksums))
    (archive / "READY").write_text("1\n")

    def already_downloaded(host, remote_root, destination, selected, progress):
        assert selected == version
        assert destination == str(source_root)
        if progress:
            progress(1)
            progress(2)
        return f"取得済み: {archive}"

    monkeypatch.setattr(vps, "copy_version", already_downloaded)
    progress = []
    result = vps.import_version("vps", "/srv", version, progress.append)
    assert "sourcesへ登録" in result
    assert progress == [1, 2, 3, 4, 5]
    source = core.DATA / "sources" / version
    assert list(core.DATA.rglob(f"{sample}.jpg")) == [source / "images" / f"{sample}.jpg"]
    assert (source / "labels" / f"{sample}.txt").read_text() == "0 0.5 0.5 0.4 0.4\n"
    assert (source / "labels.org" / f"{sample}.txt").read_text() == "0 0.5 0.5 0.4 0.4\n"
    assert not (source / "annotations").exists()
    assert (source / "meta" / "source" / f"{sample}.json").exists()
    assert [row['name'] for row in json.loads((source/'meta'/'classes.json').read_text())][-3:]==['red5m','red5p','red5s']
    assert core.classes(source)[-3:]==['red5m','red5p','red5s']
    assert any(item["path"] == f"sources/{version}" and item["editable"] for item in core.datasets())
    with registry.connect() as db:
        row = db.execute("SELECT status FROM annotations").fetchone()
    assert row[0] == "provisional"
    (source / "labels" / f"{sample}.txt").write_text("1 0.5 0.5 0.4 0.4\n")
    (source / "annotations" / "yolo").mkdir(parents=True)
    (source / "annotations" / "yolo" / f"{sample}.txt").write_text("0 0.5 0.5 0.4 0.4\n")
    assert "登録済み" in vps.import_version("vps", "/srv", version)
    assert (source / "labels" / f"{sample}.txt").read_text().startswith("1 ")
    assert (source / "labels.org" / f"{sample}.txt").read_text().startswith("0 ")
    assert not (source / "annotations").exists()
    assert vps.verify(archive, imported=True) is None


def test_preserve_original_annotations_keeps_empty_labels_and_rejects_changes(tmp_path):
    source = tmp_path / "source"
    sample = "00000000-0000-4000-8000-000000000001"
    (source / "meta").mkdir(parents=True)
    (source / "annotations" / "yolo").mkdir(parents=True)
    (source / "meta" / "manifest.jsonl").write_text(json.dumps({"sampleId": sample}) + "\n")
    (source / "annotations" / "yolo" / f"{sample}.txt").write_text("")

    vps.preserve_original_annotations(source)
    saved = source / "labels.org" / f"{sample}.txt"
    assert saved.is_file() and saved.read_text() == ""
    (source / "annotations" / "yolo" / f"{sample}.txt").write_text("0 0.5 0.5 0.4 0.4\n")
    with pytest.raises(ValueError, match="一致しません"):
        vps.preserve_original_annotations(source)
    assert saved.read_text() == ""
