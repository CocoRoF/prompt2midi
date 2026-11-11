"""Genre presets and instrument-role rules for the expander (docs/expander.md).

Faithful to the genre table in docs/프롬프트투미디/intent-컴파일러-가능성표.md section 2: a preset only sets what that table
states; everything else is left to the mood preset or the global default. The sound of these presets was NOT checked by listening.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GenrePreset:
    drums: str | None = None  # DrumStyle value; None = this genre brings no drum track (classical)
    has_drums: bool = True  # False: the genre explicitly has no drum track
    mode: str | None = None
    bpm: int | None = None
    energy: float | None = None
    progression: tuple[list[int], list[int]] | None = None  # scale degrees of sections A and B
    seventh: bool = False  # stack diatonic 7ths on the chords
    force_quality: str | None = None  # e.g. "7": dominant 7th chords on every degree (blues), even if not diatonic
    roles: dict[str, str] = field(default_factory=dict)  # default instrument per role (melody/chords/pad/bass)
    table_row: str = ""  # what the feasibility table says, for the docs


GENRES: dict[str, GenrePreset] = {
    "pop": GenrePreset(drums="rock_basic", mode="major", progression=([1, 5, 6, 4], [4, 1, 5, 1]), table_row="rock_basic · major I-V-vi-IV"),
    "rock": GenrePreset(drums="rock_basic", energy=0.8, roles={"melody": "steel_guitar", "bass": "electric_bass"}, table_row="rock_basic · high energy · electric guitar"),
    "jazz": GenrePreset(drums="shuffle", seventh=True, progression=([2, 5, 1, 6], [4, 7, 3, 6]), roles={"melody": "piano"}, table_row="shuffle · more 7th chords · piano"),
    "blues": GenrePreset(drums="shuffle", mode="major", progression=([1, 4, 1, 5], [4, 4, 1, 5]), force_quality="7", table_row="shuffle · dominant 7th chords"),
    "ballad": GenrePreset(drums="half_time", bpm=70, energy=0.3, roles={"melody": "piano", "pad": "strings"}, table_row="half_time · slow, low energy · piano + strings"),
    "bossa_nova": GenrePreset(drums="bossa", seventh=True, progression=([2, 5, 1, 6], [1, 6, 2, 5]), table_row="bossa · m7 / 7 chords"),
    "electronic": GenrePreset(drums="four_on_floor", roles={"melody": "synth_lead", "bass": "synth_bass"}, table_row="four_on_floor · synth lead + synth bass"),
    "ambient": GenrePreset(drums="half_time", energy=0.1, roles={"pad": "synth_pad"}, table_row="half_time · pad-centred · very low energy"),
    "classical": GenrePreset(has_drums=False, roles={"melody": "piano", "pad": "strings"}, table_row="no drum track · strings / piano"),
    "lofi": GenrePreset(drums="half_time", energy=0.25, seventh=True, roles={"melody": "electric_piano"}, table_row="half_time · electric_piano · low energy"),
}

# ---- instrument roles ----
BASS_INSTRUMENTS = ("upright_bass", "electric_bass", "synth_bass")
PAD_ONLY = ("synth_pad",)
MELODY_OK = ("piano", "electric_piano", "nylon_guitar", "steel_guitar", "violin", "strings", "trumpet", "sax", "flute", "clarinet", "marimba", "synth_lead", "organ")
CHORDS_OK = ("piano", "electric_piano", "organ", "nylon_guitar", "steel_guitar", "marimba", "strings", "violin")
PAD_OK = ("strings", "organ", "synth_pad")
ROLE_ORDER = ("melody", "chords", "pad", "bass", "drums")


def assign_instruments(listed: list[str]) -> tuple[dict[str, str], bool, list[tuple[str, str]]]:
    """Explicit instrument list -> ({role: instrument}, drums_listed, [(instrument, why ignored)]).

    Deterministic, in list order, each role filled once:
      * 'drums' sets drums_listed (the style is chosen separately);
      * a bass instrument takes the bass role if it is free;
      * synth_pad takes the pad role if it is free;
      * any other instrument takes the first free role among melody -> chords -> pad that it is suited for
        (suitability lists above); otherwise it is ignored.
    """
    roles: dict[str, str] = {}
    ignored: list[tuple[str, str]] = []
    drums = False
    for ins in listed:
        if ins == "drums":
            drums = True
        elif ins in BASS_INSTRUMENTS:
            if "bass" in roles:
                ignored.append((ins, "bass role already taken"))
            else:
                roles["bass"] = ins
        elif ins in PAD_ONLY:
            if "pad" in roles:
                ignored.append((ins, "pad role already taken"))
            else:
                roles["pad"] = ins
        else:
            for role, ok in (("melody", MELODY_OK), ("chords", CHORDS_OK), ("pad", PAD_OK)):
                if role not in roles and ins in ok:
                    roles[role] = ins
                    break
            else:
                ignored.append((ins, "no free role it is suited for"))
    return roles, drums, ignored
