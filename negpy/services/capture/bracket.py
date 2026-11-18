"""HDR bracket capture: the exposure of each shot, and the bracket a calibration reads off the film.

A bracket steps up from the frame's own exposure. The shutter takes each step first; the LED
level makes up the rest, so a step lands on its exact time even between two rungs of the body's
ladder. The merge solves the true ratios from the pictures, so a level that is not linear in
light costs no accuracy.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from negpy.services.capture.calibration import CLIP_CEILING, PWM_MAX, Roi, true_seconds, usable_ladder

#: Shots in one bracket, the base exposure included.
HDR_MIN_SHOTS = 2
HDR_MAX_SHOTS = 7
#: Stops between two shots. Past 3 the pair overlaps too little for the merge to fit a ratio.
HDR_MIN_STEP = 0.3
HDR_MAX_STEP = 3.0
HDR_DEFAULT_SHOTS = 3
HDR_DEFAULT_STEP = 2.0
#: Fraction of the clip level the densest area reaches in the longest shot: about 2.5 stops
#: under clipping, far enough above the sensor noise to carry clean detail.
DENSE_TARGET = 0.18
#: Less span than this between the densest area and its target needs no bracket.
MIN_SPAN_STOPS = 0.5
#: The brightest area reads outside this band when the frame was not metered first.
BRIGHT_LOW, BRIGHT_HIGH = 0.5, 0.98
#: Where a bracket's RAW files go once the merged DNG replaces them in the roll folder.
BRACKET_FOLDER = "HDR Brackets"


@dataclass(frozen=True)
class BracketShot:
    """One exposure of a bracket. `levels` scale the base levels; `short_stops` is how far the
    shot falls short of its step because the slowest shutter and full LED power could not reach it."""

    index: int
    shutter: str
    levels: tuple[int, ...]
    stops: float
    short_stops: float = 0.0


@dataclass(frozen=True)
class BracketReading:
    """The film's range read from one frame at the scan's exposure, and the bracket it needs."""

    bright: float  # the brightest area's level in its brightest channel, as a fraction of the clip level
    dense: float  # the densest area's median in its darkest channel, as a fraction of the clip level
    stops: float  # the widest bright-to-dense range over the channels
    shots: int  # 1 = one exposure holds the film
    step: float


def clamp_shots(count: float) -> int:
    return max(HDR_MIN_SHOTS, min(HDR_MAX_SHOTS, int(round(count))))


def clamp_step(step: float) -> float:
    return max(HDR_MIN_STEP, min(HDR_MAX_STEP, float(step)))


def _scaled(levels: Sequence[int], gain: float) -> tuple[int, ...]:
    """Each lit level times `gain`, at least 1 so a lit LED stays lit. An unlit one stays 0."""
    return tuple(0 if lv <= 0 else max(1, min(PWM_MAX, int(round(lv * gain)))) for lv in levels)


def plan_bracket(base_shutter: str, levels: Sequence[int], count: int, step: float, ladder: Sequence[str]) -> tuple[BracketShot, ...]:
    """The bracket's shots, shortest first. The first is the base exposure unchanged.

    Each later shot takes the fastest shutter at least as long as its target time and dims the
    LEDs to the exact time. Past the slowest shutter the LEDs brighten instead, all by one gain
    so the light keeps its color, up to the brightest one at full power. With no `levels` the
    light is the operator's, and the shutter is the whole step.
    """
    ladder = usable_ladder(tuple(str(v).strip() for v in ladder))
    if not ladder:
        raise ValueError("the camera reports no shutter speeds")
    if not base_shutter:
        raise ValueError("the bracket needs the base shutter")
    count = clamp_shots(count)
    step = clamp_step(step)
    base_levels = tuple(int(v) for v in levels)
    base_s = true_seconds(base_shutter, ladder)
    if base_s <= 0:
        raise ValueError(f"the base shutter {base_shutter!r} is not a time")
    shots = [BracketShot(0, base_shutter, base_levels, 0.0)]
    lit = [lv for lv in base_levels if lv > 0]
    for k in range(1, count):
        stops = k * step
        target = base_s * 2.0**stops
        shutter = next((c for c in ladder if true_seconds(c, ladder) >= target * 0.97), ladder[-1])
        gain = target / true_seconds(shutter, ladder)
        short = 0.0
        if gain > 1.0:
            reach = PWM_MAX / max(lit) if lit else 1.0
            if gain > reach:
                short = math.log2(gain / reach)
            gain = min(gain, reach)
        shots.append(BracketShot(k, shutter, _scaled(base_levels, gain) if lit else base_levels, stops, short))
    return tuple(shots)


def plan_from_density(dense_fraction: float) -> tuple[int, float]:
    """(shots, step) that bring the densest area to `DENSE_TARGET` in the longest shot.

    `dense_fraction` is the area's level at the base exposure, as a fraction of the clip level.
    One shot means the base exposure already holds it. The step is the widest that fits the span,
    rounded up to a third of a stop.
    """
    if dense_fraction <= 0:
        return HDR_MAX_SHOTS, HDR_MAX_STEP
    span = math.log2(DENSE_TARGET / dense_fraction)
    if span < MIN_SPAN_STOPS:
        return 1, 0.0
    shots = min(HDR_MAX_SHOTS, math.ceil(span / HDR_MAX_STEP - 1e-9) + 1)
    step = math.ceil(span / (shots - 1) * 3 - 1e-9) / 3
    return shots, round(clamp_step(step), 2)


def _patch(img: np.ndarray, roi: Roi) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi.pixels(w, h)
    return img[y0:y1, x0:x1].reshape(-1, img.shape[2]).astype(np.float64)


def read_bracket(img: np.ndarray, bright_roi: Roi, dense_roi: Roi) -> BracketReading:
    """Meter the brightest and the densest area of one demosaiced frame (`HxWx3`, 0..CLIP_CEILING).

    The bright area reads its 99.9th percentile, as the meter reads a base; the dense area reads
    its median, the level a bracket must lift out of the noise. The darkest channel of the dense
    area decides the bracket.
    """
    bright = np.percentile(_patch(img, bright_roi), 99.9, axis=0) / CLIP_CEILING
    dense = np.median(_patch(img, dense_roi), axis=0) / CLIP_CEILING
    lit = bright > 0.01  # a channel the light leaves dark says nothing about density
    if not lit.any():
        raise ValueError("the brightest area reads no light")
    floor = 1.0 / CLIP_CEILING
    stops = float(max(math.log2(b / max(d, floor)) for b, d in zip(bright[lit], dense[lit])))
    dense_min = float(max(dense[lit].min(), floor))
    shots, step = plan_from_density(dense_min)
    return BracketReading(bright=float(bright.max()), dense=dense_min, stops=max(0.0, stops), shots=shots, step=step)


def shot_stem(frame_stem: str, index: int) -> str:
    """The file stem of a bracket's shot: `_H1` is the base exposure, the longest shot last."""
    return f"{frame_stem}_H{index + 1}"


def bracket_folder(output_folder: str) -> str:
    return os.path.join(output_folder, BRACKET_FOLDER)
