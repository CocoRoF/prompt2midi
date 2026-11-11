"""Intent -> MusicSpec, deterministic. Rules and tables: docs/expander.md.

Order for every value:  explicit in the Intent  >  genre preset  >  mood preset  >  global default.
Same Intent -> same MusicSpec (the seed is derived from the Intent's content).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..intent import Intent
from ..presets import DEFAULT_TONIC, MOODS, MOTIFS, PROGRESSIONS, diatonic_chord
from ..spec import PITCH_CLASS, MusicSpec
from ..validate import Issue, validate_spec
from .presets import GENRES, assign_instruments

DEFAULT_BARS = 12
DEFAULT_MOOD = "calm"  # global default mood preset when the Intent names none
MAX_SECONDS = 300.0
GLOBAL_TIME_SIGNATURE = "4/4"
CHORD_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


class ExpandError(ValueError):
    """The expansion did not validate (a bug in the presets, never a user error)."""

    def __init__(self, message: str, issues: list[Issue]):
        super().__init__(message)
        self.issues = issues


@dataclass
class Decision:
    field: str
    value: Any
    source: str  # "intent" | "genre:<id>" | "mood:<id>" | "default"
    rule: str

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value, "source": self.source, "rule": self.rule}


@dataclass
class Expansion:
    spec: MusicSpec
    explain: list[Decision] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def explain_dicts(self) -> list[dict[str, Any]]:
        return [d.as_dict() for d in self.explain]

    def decision(self, field_name: str) -> Decision:
        return next(d for d in self.explain if d.field == field_name)


def derive_seed(intent: Intent) -> int:
    """32-bit seed from the Intent's musical content (`conflicts` and `raw` are metadata: rewording does not change the music)."""
    text = json.dumps(intent.model_dump(mode="json", exclude={"conflicts", "raw"}), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


def _split(total: int) -> list[int]:
    """Section sizes A, B, A2 for `total` bars (one section when total <= 3)."""
    if total <= 3:
        return [total]
    return [(total + 2) // 3, (total + 1) // 3, total // 3]


def _beats_per_bar(sig: str) -> float:
    n, d = (int(x) for x in sig.split("/"))
    return 4 * n / d


def expand(intent: Intent) -> Expansion:
    ex: list[Decision] = []

    def note(field_name: str, value: Any, source: str, rule: str) -> None:
        ex.append(Decision(field_name, value, source, rule))

    moods = [m.value for m in intent.mood]
    genres = [g.value for g in intent.genre]
    primary_mood = moods[0] if moods else DEFAULT_MOOD
    mood_src = f"mood:{primary_mood}" if moods else "default"
    _t, mood_energy, mood_mode, mood_bpm, motif_key = MOODS[primary_mood]
    if len(moods) > 1:
        energies = [MOODS[m][1] for m in moods]
        mood_energy = round(sum(energies) / len(energies), 3)
        note("mood", moods, "intent", f"several moods: the first ({moods[0]}) decides mode/tempo/motif, energy is the mean of all ({mood_energy})")
    elif not moods:
        note("mood", DEFAULT_MOOD, "default", "no mood stated: global default mood preset")
    else:
        note("mood", moods, "intent", f"mood preset {primary_mood}: energy {mood_energy}, mode {mood_mode}, bpm {mood_bpm}, motif {motif_key}")
    g = GENRES[genres[0]] if genres else None
    g_src = f"genre:{genres[0]}" if genres else "default"
    if genres:
        note("genre", genres, "intent", "the first genre decides drums/progression/instruments/tempo; " + ("others are ignored: " + ", ".join(genres[1:]) if len(genres) > 1 else "single genre"))

    # ---- key ----
    if intent.mode is not None:
        mode, mode_src, mode_rule = intent.mode.value, "intent", "explicit mode"
    elif g is not None and g.mode is not None:
        mode, mode_src, mode_rule = g.mode, g_src, "genre preset mode"
    else:
        mode, mode_src, mode_rule = mood_mode, mood_src, "mood preset mode"
    note("mode", mode, mode_src, mode_rule)
    if intent.tonic is not None:
        tonic, t_src, t_rule = intent.tonic.value, "intent", "explicit tonic"
    else:
        tonic, t_src, t_rule = DEFAULT_TONIC[mode], "default", f"default tonic of {mode}"
    note("tonic", tonic, t_src, t_rule)
    tonic_pc = PITCH_CLASS[tonic]

    # ---- tempo, meter, energy ----
    if intent.tempo_bpm is not None:
        bpm, b_src, b_rule = intent.tempo_bpm, "intent", "explicit tempo"
    elif g is not None and g.bpm is not None:
        bpm, b_src, b_rule = g.bpm, g_src, "genre preset tempo"
    else:
        bpm, b_src, b_rule = mood_bpm, mood_src, "mood preset tempo"
    note("tempo_bpm", bpm, b_src, b_rule)
    sig = intent.time_signature or GLOBAL_TIME_SIGNATURE
    note("time_signature", sig, "intent" if intent.time_signature else "default", "explicit meter" if intent.time_signature else "global default meter")
    if g is not None and g.energy is not None:
        energy, e_src, e_rule = g.energy, g_src, "genre preset energy"
    else:
        energy, e_src, e_rule = mood_energy, mood_src, "mood preset energy"
    note("energy", energy, e_src, e_rule)

    # ---- chords and form ----
    explicit_chords = intent.chords is not None
    if explicit_chords:
        chords_a = chords_b = list(intent.chords)  # type: ignore[arg-type]
        note("chords", chords_a, "intent", "explicit chords, used by every section")
    else:
        prog = g.progression if g is not None and g.progression is not None else PROGRESSIONS[mode]
        seventh = bool(g and g.seventh)
        force = g.force_quality if g else None

        def spell(deg: int) -> str:
            if force:
                return CHORD_NAMES[(tonic_pc + _degree_semitones(mode, deg)) % 12] + force
            return diatonic_chord(mode, tonic_pc, deg, seventh)

        chords_a, chords_b = [spell(d) for d in prog[0]], [spell(d) for d in prog[1]]
        src = g_src if (g is not None and (g.progression is not None or seventh or force)) else "default"
        note("chords", {"A": chords_a, "B": chords_b}, src,
             "genre progression" + (" with sevenths" if seventh else "") + (f" as {force} chords" if force else "") if src != "default" else f"default {mode} progression")
    if intent.bars is not None:
        total, bars_src, bars_rule = intent.bars, "intent", "explicit bars"
    elif explicit_chords:
        total, bars_src, bars_rule = max(DEFAULT_BARS, 3 * len(chords_a)), "default", "explicit chords: three sections of len(chords) bars (at least 12)"
    else:
        total, bars_src, bars_rule = DEFAULT_BARS, "default", "global default 12 bars"
    max_bars = max(1, int(MAX_SECONDS * bpm / (60 * _beats_per_bar(sig))))
    if total > max_bars:
        note("bars", max_bars, "default", f"{total} bars would last more than {MAX_SECONDS:.0f} s at {bpm} bpm: clamped (the only case where an explicit value is not kept)")
        total = max_bars
    else:
        note("bars", total, bars_src, bars_rule)
    sizes = _split(total)
    names = ["A", "B", "A2"]
    energies = [energy, min(1.0, energy + 0.2), energy]
    chord_sets = [chords_a, chords_b, chords_a]
    sections = [{"name": names[i], "bars": n, "chords": chord_sets[i], "energy": round(energies[i], 2)} for i, n in enumerate(sizes)]

    # ---- instruments and roles ----
    drum_style = None
    if intent.instruments:
        listed = [i.value for i in intent.instruments]
        roles, drums_listed, ignored = assign_instruments(listed)
        note("instruments", roles | ({"drums": True} if drums_listed else {}), "intent", "explicit ensemble: only the listed instruments play (role order: melody > chords > pad; bass/pad types first)")
        for ins, why in ignored:
            note("instruments.ignored", ins, "intent", why)
        want_drums = drums_listed
        if want_drums:
            drum_style, d_src = _drum_style(g, g_src, energy)
    else:
        roles = {}
        if g is not None:
            roles.update(g.roles)
        melody = roles.get("melody", "piano")
        roles["melody"] = melody
        if "pad" not in roles and primary_mood in ("meditative", "epic"):
            roles["pad"] = "synth_pad"
        if "chords" not in roles and "pad" not in roles:
            roles["chords"] = "electric_piano" if melody == "piano" else "piano"
        if g is not None and g.has_drums:
            want_drums = True
        elif g is not None and not g.has_drums:
            want_drums = False
        else:
            want_drums = energy >= 0.7
        if want_drums:
            drum_style, d_src = _drum_style(g, g_src, energy)
        if "bass" not in roles and (want_drums or energy >= 0.4):
            roles["bass"] = "electric_bass"
        note("instruments", roles | ({"drums": True} if want_drums else {}), g_src if (g and g.roles) else "default",
             "genre preset instruments, then global defaults (melody piano; chords if no pad; pad for meditative/epic; bass if drums or energy >= 0.4; drums from genre or energy >= 0.7)")
    if want_drums:
        note("drums", drum_style, d_src, "drum style from genre preset" if d_src.startswith("genre") else "drum style from energy (< 0.35 half_time, otherwise rock_basic)")

    # ---- tracks and motif ----
    def tracks(motif_name: str) -> list[dict[str, Any]]:
        degrees, rhythm = MOTIFS[motif_name]
        out: list[dict[str, Any]] = []
        for role in ("melody", "chords", "pad", "bass"):
            if role in roles:
                t: dict[str, Any] = {"role": role, "instrument": roles[role]}
                if role == "melody":
                    t["motif"] = {"degrees": list(degrees), "rhythm": list(rhythm)}
                out.append(t)
        if want_drums:
            out.append({"role": "drums", "style": drum_style})
        return out

    title_bits = [primary_mood.capitalize()] + ([genres[0].replace("_", " ")] if genres else []) + [tonic, mode.replace("_", " ")]
    base = {"version": "1", "title": " ".join(title_bits)[:60], "seed": derive_seed(intent), "tempo_bpm": bpm,
            "time_signature": [int(x) for x in sig.split("/")], "key": {"tonic": tonic, "mode": mode}, "sections": sections}
    last_issues: list[Issue] = []
    for motif_name in dict.fromkeys([motif_key, "arch_slow"]):
        spec_dict = dict(base, tracks=tracks(motif_name))
        spec, report = validate_spec(spec_dict)
        if spec is not None and report.ok:
            note("motif", motif_name, mood_src if motif_name == motif_key else "default",
                 "mood preset motif" if motif_name == motif_key else f"the {motif_key} motif does not fit the melody instrument's register: narrower arch_slow motif")
            for iss in intent.conflicts:
                note("conflicts", {"fields": [f.value for f in iss.fields], "reason": iss.reason}, "intent", "reported by the provider; passed through, does not change the result")
            return Expansion(spec, ex, report.warnings)
        last_issues = report.errors if spec is not None else report.issues
    raise ExpandError("expansion did not validate: " + "; ".join(f"{i.path}: {i.message}" for i in last_issues), last_issues)


def _drum_style(g, g_src: str, energy: float) -> tuple[str, str]:
    if g is not None and g.drums is not None:
        return g.drums, g_src
    return ("half_time" if energy < 0.35 else "rock_basic"), "default"


def _degree_semitones(mode: str, degree: int) -> int:
    from ..spec import MODE_INTERVALS

    return MODE_INTERVALS[mode][degree - 1]
