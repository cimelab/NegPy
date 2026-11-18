"""Turn a captured HDR bracket into one RAW file: a linear DNG at the reference shot's exposure.

The ratios are solved from the pictures, as the Film Strip's HDR merge solves them. The DNG keeps
the camera's own color data (its XYZ matrix and the as-shot white balance), so it opens like the
camera's RAW: in camera space, with no white balance baked in.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional, Sequence

import numpy as np

from negpy.services.capture.bracket import bracket_folder

_LINEAR_RAW = 34892
_D65 = 21
_WHITE = 65535


def solve_bracket(paths: Sequence[str]) -> tuple[list[str], tuple[float, ...]]:
    """(paths with the exposure reference first, each one's exposure relative to it).

    Half-size decodes: a ratio is one number per frame, and every frame decodes on the same
    neutral white balance, so a gain they share cancels in it.
    """
    from negpy.features.hdr.logic import ExposureStats, choose_reference, measure_exposure, solve_ratios
    from negpy.infrastructure.capture.raw_demosaic import linear_demosaic

    frames = [linear_demosaic(p, half_size=True) for p in paths]
    if len({f.shape for f in frames}) > 1:
        raise ValueError("the bracket's shots differ in size")
    stats = [ExposureStats(p, *measure_exposure(f)) for p, f in zip(paths, frames)]
    reference = choose_reference(stats)
    ratios = solve_ratios(frames, reference)
    order = [reference, *[i for i in range(len(paths)) if i != reference]]
    return [paths[i] for i in order], tuple(float(ratios[i]) for i in order)


def _rational(value: float, den: int = 10000) -> tuple[int, int]:
    return int(round(value * den)), den


def _exposure_rational(seconds: float) -> tuple[int, int]:
    return (1, max(1, int(round(1.0 / seconds)))) if 0 < seconds < 1 else _rational(seconds, 1000)


def dng_tags(
    *,
    model: str,
    cam_xyz: Optional[Sequence[Sequence[float]]],
    as_shot_wb: Optional[Sequence[float]],
    exposure_s: float = 0.0,
    iso: int = 0,
    make: str = "",
    datetime: str = "",
) -> list:
    """The tifffile `extratags` of a 3-sample LinearRaw DNG with a camera's color data. The
    exposure tags mark it a camera's file, which the loaders decode with its as-shot color."""
    tags = [
        (50706, "B", 4, (1, 4, 0, 0), True),  # DNGVersion
        (50707, "B", 4, (1, 1, 0, 0), True),  # DNGBackwardVersion
        (50708, "s", 0, " ".join(p for p in (make, model) if p) or "NegPy HDR", True),  # UniqueCameraModel
        (50714, "H", 1, (0,), True),  # BlackLevel
        (50717, "H", 1, (_WHITE,), True),  # WhiteLevel
        (274, "H", 1, (1,), True),  # Orientation: the merge is already upright
    ]
    if make:
        tags.append((271, "s", 0, make, True))
    if model:
        tags.append((272, "s", 0, model, True))
    if datetime:
        tags.append((306, "s", 0, datetime, True))
    if cam_xyz is not None:
        flat = [v for row in list(cam_xyz)[:3] for v in row[:3]]
        if len(flat) == 9 and np.all(np.isfinite(flat)) and max(abs(v) for v in flat) > 1e-9:
            tags.append((50721, "2i", 9, tuple(x for v in flat for x in _rational(v)), True))  # ColorMatrix1
            tags.append((50778, "H", 1, (_D65,), True))  # CalibrationIlluminant1
    if as_shot_wb is not None and len(as_shot_wb) >= 3 and min(as_shot_wb[:3]) > 0:
        r, g, b = (float(v) for v in as_shot_wb[:3])
        neutral = (g / r, 1.0, g / b)  # AsShotNeutral is the inverse of the white balance gains
        tags.append((50728, "2I", 3, tuple(x for v in neutral for x in _rational(v, 1_000_000)), True))
    if exposure_s > 0:
        tags.append((33434, "2I", 1, _exposure_rational(exposure_s), True))  # ExposureTime
    if iso > 0:
        tags.append((34855, "H", 1, (int(iso),), True))  # ISOSpeedRatings
    return tags


def write_linear_dng(rgb: np.ndarray, output_path: str, tags: list) -> str:
    """Write `rgb` (float32 [0, 1], camera space) as an uncompressed 16-bit LinearRaw DNG."""
    import tifffile

    data = np.round(np.clip(rgb[..., :3], 0.0, 1.0) * _WHITE).astype(np.uint16)
    tmp = output_path + ".part"
    tifffile.imwrite(
        tmp,
        np.ascontiguousarray(data),
        photometric=_LINEAR_RAW,
        planarconfig="contig",
        subfiletype=0,
        software="NegPy",
        extratags=tags,
        metadata=None,
    )
    os.replace(tmp, output_path)
    return output_path


def merge_to_dng(paths: Sequence[str], ratios: Sequence[float], output_path: str, *, exposure_s: float = 0.0, iso: int = 0) -> str:
    """Merge a solved bracket (reference first) into one DNG at the reference's exposure, so the
    film's brightest area keeps its level and the recovered range lands in the shadows."""
    import rawpy

    from negpy.features.hdr.models import HdrConfig
    from negpy.services.export.linear_output import _decode_hdr

    hdr = HdrConfig(hdr_enabled=True, hdr_paths=tuple(paths[1:]), hdr_ratios=tuple(ratios), hdr_align=True, hdr_anchor_ev=0.0)
    rgb, wb, meta = _decode_hdr(paths[0], hdr)
    with rawpy.imread(paths[0]) as raw:
        cam_xyz = np.asarray(raw.rgb_xyz_matrix, dtype=np.float64)[:3].tolist()
        as_shot = [float(v) for v in raw.camera_whitebalance[:3]]
    tags = dng_tags(
        model=meta.model or "",
        make=meta.make or "",
        cam_xyz=cam_xyz,
        as_shot_wb=as_shot if wb is not None else None,
        exposure_s=exposure_s,
        iso=iso,
        datetime=meta.datetime or "",
    )
    return write_linear_dng(rgb, output_path, tags)


def stash_bracket(paths: Sequence[str], output_folder: str) -> list[str]:
    """Move a merged bracket's RAW files into the roll's bracket folder, out of the Film Strip."""
    folder = bracket_folder(output_folder)
    os.makedirs(folder, exist_ok=True)
    moved = []
    for path in paths:
        target = os.path.join(folder, os.path.basename(path))
        shutil.move(path, target)
        moved.append(target)
    return moved
