"""HDR bracket capture: the shot plan, the bracket a calibration reads, the capture sequence and its DNG."""

import math
import os
import threading

import numpy as np
import pytest

from negpy.services.capture.bracket import (
    DENSE_TARGET,
    HDR_MAX_SHOTS,
    HDR_MAX_STEP,
    plan_bracket,
    plan_from_density,
    read_bracket,
    shot_stem,
)
from negpy.services.capture.calibration import PWM_MAX, Roi, true_seconds
from negpy.services.capture.hdr_capture import dng_tags, write_linear_dng
from negpy.services.capture.service import CaptureError, CaptureService

# A third-stop body ladder, fastest first, up to 30 s.
LADDER = tuple(
    "1/250 1/200 1/160 1/125 1/100 1/80 1/60 1/50 1/40 1/30 1/25 1/20 1/15 1/13 1/10 1/8 1/6 1/5 1/4 0.3 0.4 0.5 0.6 0.8 "
    "1 1.3 1.6 2 2.5 3.2 4 5 6 8 10 13 15 20 25 30".split()
)


def _exposure(shot, base_levels) -> float:
    """Light times time, relative to the base levels: what the merge sees as exposure."""
    gain = shot.levels[0] / base_levels[0] if base_levels else 1.0
    return true_seconds(shot.shutter, LADDER) * gain


# ---- the shot plan ------------------------------------------------------------


def test_the_first_shot_is_the_base_exposure_unchanged():
    shots = plan_bracket("1/8", (200, 90, 80), 3, 2.0, LADDER)
    assert shots[0].shutter == "1/8"
    assert shots[0].levels == (200, 90, 80)
    assert shots[0].stops == 0.0


def test_each_shot_lands_on_its_step_by_shutter_then_light():
    base = (200, 90, 80)
    shots = plan_bracket("1/8", base, 4, 1.7, LADDER)
    t0 = true_seconds("1/8", LADDER)
    for shot in shots[1:]:
        # The shutter is never shorter than the step, and the LEDs dim to the exact time.
        assert true_seconds(shot.shutter, LADDER) >= t0 * 2**shot.stops * 0.97
        assert _exposure(shot, base) == pytest.approx(t0 * 2**shot.stops, rel=0.02)
        assert shot.short_stops == 0.0


def test_past_the_slowest_shutter_the_light_brightens_and_keeps_its_color():
    base = (120, 60, 40)
    shots = plan_bracket("20", base, 3, 1.0, LADDER)
    last = shots[-1]
    assert last.shutter == "30"
    assert max(last.levels) <= PWM_MAX
    assert last.levels[0] / last.levels[1] == pytest.approx(2.0, rel=0.05)


def test_a_step_out_of_reach_reports_how_far_short_it_falls():
    shots = plan_bracket("15", (200,), 3, 2.0, LADDER)
    # 15 s x 16 = 240 s; 30 s at full power gives 30 x 255/200 s.
    assert shots[-1].short_stops == pytest.approx(math.log2(240 / (30 * PWM_MAX / 200)), abs=0.05)


def test_without_a_light_the_shutter_is_the_whole_step():
    shots = plan_bracket("1/30", (), 3, 2.0, LADDER)
    assert [s.shutter for s in shots] == ["1/30", "1/8", "0.5"]
    assert all(s.levels == () for s in shots)


def test_a_plan_needs_a_shutter_ladder_and_a_base_time():
    with pytest.raises(ValueError):
        plan_bracket("1/8", (200,), 3, 2.0, ())
    with pytest.raises(ValueError):
        plan_bracket("", (200,), 3, 2.0, LADDER)


def test_shot_stems_count_from_the_base_exposure():
    assert shot_stem("Roll_Frame001", 0) == "Roll_Frame001_H1"
    assert shot_stem("Roll_Frame001_R", 2) == "Roll_Frame001_R_H3"


# ---- the bracket a calibration reads ---------------------------------------------


def test_a_patch_the_base_exposure_already_holds_needs_no_bracket():
    assert plan_from_density(DENSE_TARGET) == (1, 0.0)
    assert plan_from_density(0.5) == (1, 0.0)


@pytest.mark.parametrize("dense", [0.05, 0.01, 0.002, 0.0003, 0.00002])
def test_the_bracket_reaches_the_dense_target(dense):
    shots, step = plan_from_density(dense)
    span = math.log2(DENSE_TARGET / dense)
    assert 2 <= shots <= HDR_MAX_SHOTS
    assert step <= HDR_MAX_STEP
    assert (shots - 1) * step >= min(span, (HDR_MAX_SHOTS - 1) * HDR_MAX_STEP) - 0.05
    assert step * 3 == pytest.approx(round(step * 3), abs=0.05)  # thirds of a stop


def _frame(bright, dense):
    img = np.zeros((100, 100, 3), np.uint16)
    img[:10, :10] = [int(v * 65535) for v in bright]
    img[50:60, 50:60] = [int(v * 65535) for v in dense]
    return img


BRIGHT, DENSE = Roi(0.0, 0.0, 0.1, 0.1), Roi(0.5, 0.5, 0.1, 0.1)


def test_read_bracket_takes_the_darkest_channel_of_the_densest_area():
    reading = read_bracket(_frame((0.9, 0.85, 0.6), (0.02, 0.005, 0.03)), BRIGHT, DENSE)
    assert reading.bright == pytest.approx(0.9, rel=0.01)
    assert reading.dense == pytest.approx(0.005, rel=0.01)
    assert reading.stops == pytest.approx(math.log2(0.85 / 0.005), rel=0.01)
    assert (reading.shots, reading.step) == plan_from_density(reading.dense)


def test_a_channel_the_light_leaves_dark_reads_no_density():
    # Under the Scanlight's LEDs a sensor channel can stay near black; it must not set the bracket.
    reading = read_bracket(_frame((0.9, 0.8, 0.0), (0.3, 0.25, 0.0)), BRIGHT, DENSE)
    assert reading.dense == pytest.approx(0.25, rel=0.01)
    assert reading.shots == 1


def test_a_frame_with_no_light_cannot_set_a_bracket():
    with pytest.raises(ValueError):
        read_bracket(np.zeros((100, 100, 3), np.uint16), BRIGHT, DENSE)


# ---- the capture sequence ------------------------------------------------------


class _Light:
    def __init__(self):
        self.colors = []

    def set_color(self, r=0, g=0, b=0, w=0, ir=0, save=False):
        self.colors.append((r, g, b, w))

    def off(self):
        self.colors.append((0, 0, 0, 0))

    def close(self):
        pass


class _Camera:
    def __init__(self, size=9 * 1024 * 1024):
        self.size = size
        self.calls = []

    def capture(self, out_path, shutter=None, iso=None, aperture=None):
        out_path = os.path.splitext(out_path)[0] + ".ARW"
        self.calls.append((os.path.basename(out_path), shutter, iso))
        with open(out_path, "wb") as f:
            f.write(b"\0" * self.size)
        return out_path

    def close(self):
        pass


def _white(lv):
    return {"w": lv[0]}


def test_capture_bracket_shoots_shortest_first(tmp_path):
    light, cam = _Light(), _Camera()
    svc = CaptureService(light, cam, sleep=lambda _s: None)
    shots = plan_bracket("1/8", (200,), 3, 2.0, LADDER)
    seen = []
    files = svc.capture_bracket("Roll_Frame001", str(tmp_path), shots, _white, iso="100", on_shot=seen.append)
    assert seen == [0, 1, 2]
    assert [os.path.basename(p) for p in files] == ["Roll_Frame001_H1.ARW", "Roll_Frame001_H2.ARW", "Roll_Frame001_H3.ARW"]
    assert [c[1] for c in cam.calls] == ["1/8", "0.5", "2"]
    assert [c[2] for c in cam.calls] == ["100", None, None]  # ISO goes with the first shot only
    assert sorted(os.listdir(tmp_path)) == sorted(os.path.basename(p) for p in files)  # no staging left
    assert light.colors == [(0, 0, 0, 200)] * 3 + [(0, 0, 0, 0)]


def test_without_a_light_the_capture_leaves_it_alone(tmp_path):
    light = _Light()
    svc = CaptureService(light, _Camera(), sleep=lambda _s: None)
    svc.capture_bracket("Roll_Frame001", str(tmp_path), plan_bracket("1/30", (), 2, 2.0, LADDER))
    assert light.colors == []


def test_a_cancelled_bracket_leaves_no_files(tmp_path):
    cancel = threading.Event()
    svc = CaptureService(_Light(), _Camera(), sleep=lambda _s: None)
    shots = plan_bracket("1/8", (200,), 3, 2.0, LADDER)
    with pytest.raises(CaptureError):
        svc.capture_bracket(
            "Roll_Frame001", str(tmp_path), shots, _white, cancel=cancel, on_shot=lambda k: cancel.set() if k == 1 else None
        )
    assert os.listdir(tmp_path) == []


def test_a_short_file_fails_the_whole_bracket(tmp_path):
    svc = CaptureService(_Light(), _Camera(size=10), sleep=lambda _s: None)
    with pytest.raises(CaptureError):
        svc.capture_bracket("Roll_Frame001", str(tmp_path), plan_bracket("1/8", (200,), 3, 2.0, LADDER), _white)
    assert os.listdir(tmp_path) == []


# ---- the merged DNG ------------------------------------------------------------


CAM_XYZ = [[0.7, -0.2, -0.05], [-0.4, 1.2, 0.2], [-0.05, 0.1, 0.6]]


def _dng(tmp_path):
    rgb = np.random.default_rng(0).random((40, 60, 3), dtype=np.float32)
    tags = dng_tags(make="Sony", model="ILCE-7M4", cam_xyz=CAM_XYZ, as_shot_wb=[2.1, 1.0, 1.6], exposure_s=0.125, iso=100)
    return rgb, write_linear_dng(rgb, str(tmp_path / "Roll_Frame001.dng"), tags)


def test_the_merged_dng_is_linear_raw_with_its_pixels(tmp_path):
    import tifffile

    rgb, path = _dng(tmp_path)
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        assert int(page.photometric) == 34892
        assert page.tags["UniqueCameraModel"].value == "Sony ILCE-7M4"
        assert page.tags["ExposureTime"].value == (1, 8)
        np.testing.assert_allclose(page.asarray() / 65535.0, rgb, atol=1 / 65535)
    assert not os.path.exists(path + ".part")


def test_the_merged_dng_opens_as_a_camera_file_with_its_color(tmp_path):
    import tifffile

    from negpy.infrastructure.loaders.helpers import is_scanned_dng
    from negpy.infrastructure.loaders.rawpy_loader import _as_shot_color

    _rgb, path = _dng(tmp_path)
    assert not is_scanned_dng(path)  # its exposure tags keep the as-shot decode
    with tifffile.TiffFile(path) as tif:
        color = _as_shot_color(tif.pages[0])
    assert color.wb_gains == pytest.approx((2.1, 1.0, 1.6), rel=1e-4)
    assert np.allclose(color.cam_xyz, CAM_XYZ, atol=1e-4)
