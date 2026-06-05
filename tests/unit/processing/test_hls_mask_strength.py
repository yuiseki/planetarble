"""Tests for stronger HLS cloud masking (fix D).

With a deep median stack (fix E) aggressive masking is safe: masked pixels are
filled from other scenes. So the default now also masks thin cirrus and the
adjacent-cloud (cloud-edge / haze halo) class, and the cloud mask is dilated a
little to catch the semi-transparent fringe Fmask labels "clear".
"""

from __future__ import annotations

import numpy as np

from planetarble.core.models import HLSConfig
from planetarble.processing.composite import dilate_boolean
from planetarble.processing.manager import _qa_mask_value


def test_default_flags_mask_cirrus_and_adjacent_cloud() -> None:
    flags = HLSConfig().qa_mask_flags
    for needed in ("cirrus", "cloud", "adjacent_cloud", "cloud_shadow"):
        assert needed in flags
    # cirrus=1, cloud=2, adjacent_cloud=4, cloud_shadow=8, snow=16 -> 31
    assert _qa_mask_value(flags) == 1 + 2 + 4 + 8 + 16


def test_default_dilation_is_enabled() -> None:
    assert HLSConfig().cloud_mask_dilation >= 1


def test_dilate_boolean_grows_mask() -> None:
    mask = np.zeros((5, 5), dtype=bool)
    mask[2, 2] = True
    out = dilate_boolean(mask, 1)
    # 4-connected dilation grows the single pixel into a plus shape
    assert out[2, 2] and out[1, 2] and out[3, 2] and out[2, 1] and out[2, 3]
    assert not out[0, 0]
    # corners of the 3x3 neighbourhood stay unset for 4-connectivity
    assert not out[1, 1]


def test_dilate_boolean_zero_iterations_is_identity() -> None:
    mask = np.array([[True, False], [False, True]])
    assert np.array_equal(dilate_boolean(mask, 0), mask)
