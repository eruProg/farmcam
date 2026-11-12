import ast
import os
import shutil
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

SOURCE = (Path(__file__).parent.parent / 'main.py').read_text(encoding='utf-8-sig')


def _storage_state(capture_dir, mounts, free=500e9):
    node = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == 'storage_state')

    class FakePath(PurePosixPath):
        def exists(self):
            return True

        def resolve(self):
            return self

    fake_os = SimpleNamespace(path=SimpleNamespace(ismount=lambda p: p in mounts))
    fake_shutil = SimpleNamespace(disk_usage=lambda p: SimpleNamespace(free=free))
    ns = dict(Path=FakePath, os=fake_os, shutil=fake_shutil, config=SimpleNamespace(CAPTURE_DIR=capture_dir))
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), ns)
    return ns['storage_state']()


def test_captures_on_a_mounted_ssd():
    state = _storage_state('/media/aipest/SSD/New Datasets/FarmCam captures', {'/', '/media/aipest/SSD'})
    assert state == {'path': '/media/aipest/SSD/New Datasets/FarmCam captures', 'on_ssd': True, 'free_gb': 500.0}


def test_captures_fall_back_to_the_sd_card():
    state = _storage_state('/home/aipest/New Datasets/FarmCam captures', {'/'})
    assert state['on_ssd'] is False


def test_capture_dir_follows_the_dataset_base():
    assert 'config.CAPTURE_DIR = Path(os.environ.get("FARMCAM_CAPTURE_DIR") or DATASET_BASE / "FarmCam captures")' in SOURCE
    # It must be set before main.py copies it into CAPTURE_DIR.
    assert SOURCE.index('config.CAPTURE_DIR = Path(') < SOURCE.index('CAPTURE_DIR = Path(config.CAPTURE_DIR)')
