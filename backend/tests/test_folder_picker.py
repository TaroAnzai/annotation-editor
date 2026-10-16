import subprocess

import pytest

from app import folder_picker, main


def test_folder_picker_returns_selected_absolute_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(folder_picker.shutil, 'which', lambda name: '/usr/bin/zenity')
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, str(tmp_path) + '\n', '')
    monkeypatch.setattr(folder_picker.subprocess, 'run', run)

    assert main.select_import_folder(main.FolderPickerBody(current_path=str(tmp_path))) == {'path': str(tmp_path)}
    assert calls[0][0] == ['/usr/bin/zenity', '--file-selection', '--directory', '--title=取り込み元フォルダーを選択', f'--filename={tmp_path}/']
    assert calls[0][1]['capture_output'] is True


def test_folder_picker_cancel_does_not_select_a_path(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_picker.shutil, 'which', lambda name: '/usr/bin/zenity')
    monkeypatch.setattr(folder_picker.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 1, '', ''))
    assert folder_picker.choose_folder(str(tmp_path)) is None


def test_folder_picker_reports_missing_desktop_tool(monkeypatch):
    monkeypatch.setattr(folder_picker.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='zenity'):
        folder_picker.choose_folder()
