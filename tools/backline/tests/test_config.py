from pathlib import Path

import pytest

from backline.config import CONFIG_NAME, Config, find_config, load_config, song_folder


def test_load_config_resolves_paths_relative_to_the_file(tmp_path: Path):
    cfg_file = tmp_path / CONFIG_NAME
    cfg_file.write_text(
        '[paths]\njobs = "~/jobs"\ntracks = "songs"\n[tracks]\ndefault_set = "Set A"\n'
        "[count_in]\nbars = 1\nnote = 76\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.path == cfg_file
    assert cfg.jobs == Path.home() / "jobs"
    assert cfg.tracks == tmp_path / "songs"
    assert cfg.default_set == "Set A"
    assert (cfg.count_in_bars, cfg.count_in_note) == (1, 76)


def test_find_config_walks_up(tmp_path: Path):
    (tmp_path / CONFIG_NAME).write_text("")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_config(deep) == tmp_path / CONFIG_NAME


def test_defaults_without_a_file():
    cfg = Config(
        path=None,
        jobs=Path("/x"),
        tracks=None,
        default_set=None,
        count_in_bars=2,
        count_in_note=37,
        metronome="gaps",
        metronome_min_bars=1,
        metronome_silence_db=-30.0,
        metronome_volume_db=-17.0,
    )
    assert cfg.tracks is None


def test_song_folder_numbering(tmp_path: Path):
    tracks = tmp_path / "tracks"
    (tracks / "Set" / "01 - First").mkdir(parents=True)
    (tracks / "Set" / "03 - Third").mkdir()

    assert song_folder(tracks, "Set", "Fourth") == tracks / "Set" / "04 - Fourth"
    assert song_folder(tracks, "Set", "Second", number=2) == tracks / "Set" / "02 - Second"
    assert song_folder(tracks, "Set", "third") == tracks / "Set" / "03 - Third", "reuses by title"
    (tracks / "Set" / "05 - Fotografía").mkdir()
    assert song_folder(tracks, "Set", "Fotografia") == tracks / "Set" / "05 - Fotografía"
    assert song_folder(tracks, "New Set", "Song") == tracks / "New Set" / "01 - Song"
    with pytest.raises(FileExistsError):
        song_folder(tracks, "Set", "Other", number=1)
