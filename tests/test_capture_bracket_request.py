"""Which scans shoot an HDR bracket and merge it into one DNG."""

from negpy.desktop.workers.capture_worker import CaptureRequest, shoots_bracket


def _req(**kw) -> CaptureRequest:
    base = dict(roll_name="Roll", frame_number=7, output_folder="/tmp/roll", levels=(200, 90, 80), hdr_count=3, hdr_ev_step=2.0)
    base.update(kw)
    return CaptureRequest(**base)


def test_white_light_single_exposure_and_the_operators_light_bracket():
    assert shoots_bracket(_req(white_mode=True))
    assert shoots_bracket(_req(single_capture=True))
    assert shoots_bracket(_req(rgb_mode=False))


def test_trichrome_blend_and_one_shot_take_no_bracket():
    assert not shoots_bracket(_req())
    assert not shoots_bracket(_req(blend_capture=True))
    assert not shoots_bracket(_req(white_mode=True, hdr_count=1))
