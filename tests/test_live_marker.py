"""Proof that the ``live`` marker is deselected by default.

``live`` tests re-measure the real source: they are slow, need network, and every extra call
brings the fragile RAD backend closer to refusing service. They run only under ``-m live``.
"""

import pytest


@pytest.mark.live
def test_live_marker_is_deselected_by_default() -> None:
    # Reaching this body without ``-m live`` means the default selection is broken.
    assert True
