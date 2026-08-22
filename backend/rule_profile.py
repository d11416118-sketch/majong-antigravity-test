from dataclasses import dataclass


@dataclass(frozen=True)
class RuleProfile:
    id: str
    display_name: str
    match_winds: int = 1
    dead_wall_size: int = 16
    dealer_continues_on_draw: bool = True
    base_score: int = 1
    minimum_fan: int = 0
    allow_multi_hu: bool = False
    guoshui_mode: str = "strict_safe_draw_discard"
    qiang_gang_fan: int = 1
    gang_shang_fan: int = 1
    haidi_fan: int = 1
    hedi_fan: int = 1
    tian_hu_fan: int = 24
    di_hu_fan: int = 16
    ren_hu_fan: int = 8
    declared_ting_fan: int = 1
    di_ting_fan: int = 4
    flower_win_fan: int = 8

    def to_public_json(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "match_winds": self.match_winds,
            "dead_wall_size": self.dead_wall_size,
            "base_score": self.base_score,
            "minimum_fan": self.minimum_fan,
            "allow_multi_hu": self.allow_multi_hu,
            "guoshui_mode": self.guoshui_mode,
            "special_fans": {
                "qiang_gang": self.qiang_gang_fan,
                "gang_shang": self.gang_shang_fan,
                "haidi": self.haidi_fan,
                "hedi": self.hedi_fan,
                "tian_hu": self.tian_hu_fan,
                "di_hu": self.di_hu_fan,
                "ren_hu": self.ren_hu_fan,
                "declared_ting": self.declared_ting_fan,
                "di_ting": self.di_ting_fan,
                "flower_win": self.flower_win_fan,
            },
        }


STANDARD_TW16_V1 = RuleProfile(
    id="standard_tw16_v1",
    display_name="台灣 16 張－東風標準場",
)


RULE_PROFILES = {STANDARD_TW16_V1.id: STANDARD_TW16_V1}


def get_rule_profile(profile_id: str = STANDARD_TW16_V1.id) -> RuleProfile:
    return RULE_PROFILES.get(profile_id, STANDARD_TW16_V1)
