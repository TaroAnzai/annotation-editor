"""Open a local desktop folder chooser for the loopback web UI."""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path


_picker_lock = threading.Lock()


def choose_folder(current_path: str = '') -> str | None:
    executable = shutil.which('zenity')
    if not executable:
        raise RuntimeError('フォルダー選択には zenity が必要です。パスを直接入力することもできます。')
    if not _picker_lock.acquire(blocking=False):
        raise RuntimeError('フォルダー選択ダイアログが既に開いています')
    try:
        initial = Path(current_path).expanduser() if current_path else Path.home()
        if not initial.is_dir():
            initial = initial.parent if initial.parent.is_dir() else Path.home()
        result = subprocess.run(
            [executable, '--file-selection', '--directory', '--title=取り込み元フォルダーを選択', f'--filename={initial}/'],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise RuntimeError('フォルダー選択ダイアログを開けませんでした: ' + (result.stderr.strip() or str(result.returncode)))
        selected = result.stdout.strip()
        if not selected or not Path(selected).is_absolute() or not Path(selected).is_dir():
            raise RuntimeError('選択したフォルダーを確認できませんでした')
        return selected
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('フォルダー選択がタイムアウトしました') from exc
    finally:
        _picker_lock.release()
