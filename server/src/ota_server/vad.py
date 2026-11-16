"""Energy VAD: cut a turn down to its speech, or reject it as silence or noise.

Input is device PCM 16 kHz s16le mono (docs/protocol.md). Samples are read with native
`array('h')`, which matches on little-endian hosts. Stdlib only: `audioop` is gone in 3.13.
ENERGY_THRESHOLD is a start value above the M2 noise level (300); tune it on device audio.
"""

import math
from array import array

from .protocol import SAMPLE_RATE

ENERGY_THRESHOLD = 500  # frame RMS at or above this is speech
MIN_SPEECH_MS = 300  # less speech than this in total is no speech
MARGIN_MS = 200  # audio kept on each side of the speech span
FRAME_MS = 20

FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
MARGIN_SAMPLES = SAMPLE_RATE * MARGIN_MS // 1000


def trim_speech(pcm: bytes) -> bytes | None:
    """Return the speech span plus margins, or None when there is too little speech."""
    if len(pcm) % 2:
        raise ValueError("PCM has an odd byte count")
    samples = array("h", pcm)
    starts = [s for s in range(0, len(samples), FRAME_SAMPLES) if _is_speech(samples, s)]
    if len(starts) * FRAME_MS < MIN_SPEECH_MS:
        return None
    first = max(0, starts[0] - MARGIN_SAMPLES)
    last = min(len(samples), starts[-1] + FRAME_SAMPLES + MARGIN_SAMPLES)
    return pcm[first * 2 : last * 2]


def _is_speech(samples: array[int], start: int) -> bool:
    """RMS >= threshold, compared as squares so no sqrt is needed."""
    frame = samples[start : start + FRAME_SAMPLES]
    return math.sumprod(frame, frame) >= ENERGY_THRESHOLD**2 * len(frame)
