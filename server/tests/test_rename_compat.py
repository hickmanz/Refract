"""The rename from LightSim to Refract: old environment variables and the old data folder keep working."""

import os
from pathlib import Path

import pytest

from refract import store
from refract.envutil import env


def test_env_prefers_new_name_and_falls_back_to_old(monkeypatch):
    monkeypatch.delenv("REFRACT_X", raising=False)
    monkeypatch.delenv("LIGHTSIM_X", raising=False)
    assert env("X") is None
    assert env("X", "d") == "d"
    monkeypatch.setenv("LIGHTSIM_X", "old")
    assert env("X") == "old"
    monkeypatch.setenv("REFRACT_X", "new")
    assert env("X") == "new"
    monkeypatch.setenv("REFRACT_X", "")  # empty counts as not set
    assert env("X") == "old"


def test_old_data_folder_is_renamed(tmp_path):
    old = tmp_path / "lightsim"
    (old / "projects" / "p1").mkdir(parents=True)
    (old / "config.json").write_text('{"dataDir": "D:\\\\keep"}')
    assert store._migrate_legacy_dir(tmp_path) == tmp_path / "refract"
    assert not old.exists()
    assert (tmp_path / "refract" / "projects" / "p1").is_dir()
    assert (tmp_path / "refract" / "config.json").read_text() == '{"dataDir": "D:\\\\keep"}'


def test_new_folder_wins_and_old_is_left_alone(tmp_path):
    (tmp_path / "lightsim").mkdir()
    (tmp_path / "refract").mkdir()
    assert store._migrate_legacy_dir(tmp_path) == tmp_path / "refract"
    assert (tmp_path / "lightsim").is_dir()


def test_nothing_to_migrate(tmp_path):
    assert store._migrate_legacy_dir(tmp_path) == tmp_path / "refract"
    assert not (tmp_path / "refract").exists()  # created later by config_dir(), not by the migration


def test_failed_rename_falls_back_to_old_folder_without_losing_data(tmp_path, monkeypatch):
    old = tmp_path / "lightsim"
    (old / "projects").mkdir(parents=True)

    def locked(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", locked)
    assert store._migrate_legacy_dir(tmp_path) == old
    assert (old / "projects").is_dir() and not (tmp_path / "refract").exists()
