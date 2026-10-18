"""Fast, request-scoped catalog for the editor's read-only opening flow.

The existing editor APIs remain available. The overview refreshes these indexes;
unknown IDs fall back to the original filesystem lookup.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import core, registry

_lock = threading.Lock()
_root = None
_datasets = {}
_images = {}


def _data_key():
    return str(core.DATA.resolve())


def _folder(d, p):
    return p/'images' if not d.get('images_direct') and (p/'images').is_dir() else p


def overview():
    datasets = core.datasets()
    statuses = registry.all_statuses()
    counts = {}
    for d in datasets:
        p = core.safe(core.DATA/d['path'])
        files = core.image_paths(_folder(d, p))
        reviewed = 0
        for f in files:
            state = statuses.get(str(f.relative_to(core.DATA)))
            if state is None:
                try:
                    state = json.loads(core.state_path(f).read_text(encoding='utf-8'))
                except FileNotFoundError:
                    state = {'status': 'unreviewed'}
            reviewed += state['status'] == 'reviewed'
        counts[d['id']] = {'reviewed': reviewed, 'images': len(files)}
    folders = core.folders()
    with _lock:
        global _root, _datasets, _images
        _root = _data_key()
        _datasets = {d['id']: (d, core.DATA/d['path']) for d in datasets}
        _images = {}
    return {'folders': folders, 'datasets': datasets, 'counts': counts}


def get_dataset(dataset_id):
    with _lock:
        cached = _datasets.get(dataset_id) if _root == _data_key() else None
    if cached:
        d, p = cached
        if p.is_dir() and not p.is_symlink() and core.safe(p) == p:
            return d, p
    return core.dataset(dataset_id)


def list_images(dataset_id):
    d, p = get_dataset(dataset_id)
    rows = core.images_for(d, p)
    folder = _folder(d, p)
    paths = {row['id']: (d, p, folder/row['name']) for row in rows}
    with _lock:
        if _root == _data_key():
            _images.update(paths)
    return rows


def get_image(image_id):
    with _lock:
        cached = _images.get(image_id) if _root == _data_key() else None
    if cached:
        d, p, f = cached
        if f.is_file() and not f.is_symlink() and core.safe(f) == f and core.ident(f) == image_id:
            return d, p, f
    return core.image(image_id)
