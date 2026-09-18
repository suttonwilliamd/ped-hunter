from __future__ import annotations

import pytest

from ped_hunter.cli import _single_gui_instance


def test_gui_single_instance_lock_rejects_second_owner(tmp_path, monkeypatch):
    monkeypatch.setattr("ped_hunter.cli._gui_lock_path", lambda: tmp_path / "gui.lock")

    with _single_gui_instance():
        with pytest.raises(RuntimeError, match="another PED Hunter GUI instance"):
            with _single_gui_instance():
                pass
