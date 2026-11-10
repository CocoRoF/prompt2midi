import copy

import pytest

VALID_SPEC = {
    "version": "1",
    "title": "Night walk",
    "seed": 12345,
    "tempo_bpm": 96,
    "time_signature": [4, 4],
    "key": {"tonic": "C", "mode": "minor"},
    "sections": [
        {"name": "A", "bars": 4, "chords": ["Cm", "Ab", "Eb", "Bb"], "energy": 0.4},
        {"name": "B", "bars": 4, "chords": ["Fm", "Gm"], "chords_per_bar": 1, "energy": 0.7},
    ],
    "tracks": [
        {"role": "melody", "instrument": "piano",
         "motif": {"degrees": [1, 3, 5, 3], "rhythm": ["q", "e", "e", "q"]}, "register": [60, 84]},
        {"role": "bass", "instrument": "electric_bass"},
        {"role": "drums", "style": "rock_basic"},
    ],
}


@pytest.fixture
def spec_dict():
    """A fresh deep copy of a valid spec dict that tests may mutate."""
    return copy.deepcopy(VALID_SPEC)
