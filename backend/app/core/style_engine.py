"""Style engine — built-in writing templates per web novel genre."""

from typing import Any

from app.models.project import NovelGenre


class StyleEngine:
    """
    Provides genre-specific writing guidance:
      - Dialogue patterns (ratio, style)
      - Pacing curve (excitement density, low/high curve)
      - Narrative perspective
      - Excitement design (face-slapping rhythm, power-up cadence, twist frequency)
    """

    TEMPLATES: dict[str, dict[str, Any]] = {
        NovelGenre.XUANHUAN: {
            "name": "玄幻",
            "perspective": "第三人称限制视角",
            "dialogue_ratio": "30% 对话 / 70% 叙述",
            "pacing": {
                "excitement_density": "每2-3章一个小高潮，每5-8章一个大高潮",
                "low_valley_ratio": "低谷占30%，高潮占50%，过渡占20%",
                "power_up_cadence": "每5章一次实力提升暗示，每10章一次突破",
            },
            "dialogue_style": {
                "protagonist": "沉稳内敛，少说多做，关键时刻一鸣惊人",
                "antagonist": "嚣张跋扈，喜欢嘲讽，被打脸后惊怒",
                "elder": "神秘莫测，话说一半留一半",
            },
            "excitement_design": {
                "face_slap_rhythm": "嘲讽→轻视→展露实力→震惊→后续影响，周期5-8章",
                "upgrade_rhythm": "瓶颈→契机→突破→新能力展示→新挑战，周期10-15章",
                "twist_frequency": "每3-5章一个小反转，每10章一个大反转",
            },
            "common_tropes": ["废柴逆袭", "神秘传承", "扮猪吃虎", "天才对决", "远古秘辛"],
            "pov_note": "以主角视角为主，关键配角视角穿插（每3-5章一次）",
        },
        NovelGenre.URBAN: {
            "name": "都市",
            "perspective": "第一人称或第三人称限制视角",
            "dialogue_ratio": "40% 对话 / 60% 叙述",
            "pacing": {
                "excitement_density": "每2章一个小高潮，每5章一个大转折",
                "low_valley_ratio": "低谷占35%，高潮占45%，过渡占20%",
                "power_up_cadence": "每3章一次能力/资源获取",
            },
            "dialogue_style": {
                "protagonist": "机智幽默，偶有锐利，善于化解危机",
                "antagonist": "精于算计，表面客气暗地使绊",
                "female_lead": "独立有主见，对主角若即若离",
            },
            "excitement_design": {
                "face_slap_rhythm": "被看不起→暗中布局→一击翻盘→众人震惊",
                "upgrade_rhythm": "发现问题→解决→获得认可→新挑战",
                "twist_frequency": "每3章一个小反转",
            },
            "common_tropes": ["重生归来", "系统加持", "商业帝国", "逆袭打脸"],
            "pov_note": "贴近生活感，对话更口语化",
        },
        NovelGenre.SCIFI: {
            "name": "科幻",
            "perspective": "第三人称限制视角",
            "dialogue_ratio": "25% 对话 / 75% 叙述",
            "pacing": {
                "excitement_density": "每3章一个发现级高潮，每7章一个危机级高潮",
                "low_valley_ratio": "探索占40%，危机占40%，解决占20%",
                "power_up_cadence": "技术突破每5-8章",
            },
            "dialogue_style": {
                "protagonist": "理性冷静，善于分析",
                "scientist": "专业但能通俗化解释",
                "ai": "逻辑严谨，偶有出人意料的判断",
            },
            "excitement_design": {
                "discovery_rhythm": "异常→调查→发现→新认知→新问题",
                "crisis_rhythm": "预警→爆发→应对→牺牲→转机",
                "twist_frequency": "每5章一个认知反转",
            },
            "common_tropes": ["文明等级", "维度战争", "意识上传", "时间悖论"],
            "pov_note": "注重氛围营造和科技细节的真实感",
        },
        NovelGenre.WUXIA: {
            "name": "武侠",
            "perspective": "第三人称全知或限制视角",
            "dialogue_ratio": "35% 对话 / 65% 叙述",
            "pacing": {
                "excitement_density": "每2章一场武斗，每5章一场大战",
                "low_valley_ratio": "江湖行走占40%，恩怨对决占40%，感悟占20%",
                "power_up_cadence": "每5-8章武学领悟",
            },
            "dialogue_style": {
                "protagonist": "豪爽义气，言出必行",
                "villain": "阴险狡诈，或亦正亦邪",
                "master": "惜字如金，点到即止",
            },
            "excitement_design": {
                "duel_rhythm": "冲突→约战→交锋→胜负→余波",
                "enlightenment": "瓶颈→游历→顿悟→突破",
                "twist_frequency": "每4章一个恩怨反转",
            },
            "common_tropes": ["江湖恩怨", "绝学传承", "门派纷争", "义薄云天"],
            "pov_note": "武侠打斗要有招式描写，注重意境",
        },
        NovelGenre.XIANXIA: {
            "name": "仙侠",
            "perspective": "第三人称限制视角",
            "dialogue_ratio": "30% 对话 / 70% 叙述",
            "pacing": {
                "excitement_density": "每3章一个小突破，每8章一个大境界突破",
                "low_valley_ratio": "修炼占35%，战斗占35%，悟道占30%",
                "power_up_cadence": "境界突破每8-12章",
            },
            "dialogue_style": {
                "protagonist": "超然淡泊，偶有锋芒",
                "elder": "仙风道骨，话语玄机",
                "antagonist": "邪魅狂狷，或阴鸷深沉",
            },
            "excitement_design": {
                "breakthrough_rhythm": "瓶颈→心境变化→天劫→突破→新境界展示",
                "dao_comprehension": "疑惑→观察→感悟→明悟",
                "twist_frequency": "每5章一个修仙界秘密揭露",
            },
            "common_tropes": ["修仙问道", "天材地宝", "渡劫飞升", "仙魔之争"],
            "pov_note": "注重意境和修仙体系描述，但不可堆砌",
        },
        NovelGenre.SUSPENSE: {
            "name": "悬疑",
            "perspective": "第一人称或第三人称限制视角",
            "dialogue_ratio": "35% 对话 / 65% 叙述",
            "pacing": {
                "excitement_density": "每章一个线索发现，每3章一个危险升级",
                "low_valley_ratio": "调查占50%，危险占30%，真相揭露占20%",
                "power_up_cadence": "认知升级每3-5章",
            },
            "dialogue_style": {
                "protagonist": "观察力强，话少但精准",
                "witness": "欲言又止，闪烁其词",
                "culprit": "表面无辜，细节暴露",
            },
            "excitement_design": {
                "clue_rhythm": "发现异常→调查→新线索→误导→真相",
                "danger_rhythm": "察觉→跟踪→危险→逃脱→新方向",
                "twist_frequency": "每2-3章一个认知反转",
            },
            "common_tropes": ["连环谜题", "不可靠叙述", "暗线伏笔", "终极反转"],
            "pov_note": "注重氛围营造，环境描写要渲染紧张感",
        },
        NovelGenre.ROMANCE: {
            "name": "言情",
            "perspective": "第三人称交替视角（男女主）",
            "dialogue_ratio": "45% 对话 / 55% 叙述",
            "pacing": {
                "excitement_density": "每章一个互动名场面，每5章一个感情转折",
                "low_valley_ratio": "甜蜜占40%，虐心占30%，误会占30%",
                "power_up_cadence": "关系升级每5-8章",
            },
            "dialogue_style": {
                "male_lead": "外冷内热，关键时刻霸道",
                "female_lead": "独立坚韧，偶有柔软",
                "rival": "优雅但心机深重",
            },
            "excitement_design": {
                "sweet_rhythm": "偶遇→试探→靠近→心动→确认",
                "angst_rhythm": "好感→误解→推开→痛悔→和解",
                "twist_frequency": "每3章一个情感反转",
            },
            "common_tropes": ["先婚后爱", "破镜重圆", "双向暗恋", "强强联手"],
            "pov_note": "注重情感细节，内心活动描写要细腻",
        },
    }

    def get_template(self, genre: NovelGenre) -> dict[str, Any]:
        # Handle string genre from DB (PostgreSQL may return string instead of enum)
        if isinstance(genre, str) and not isinstance(genre, NovelGenre):
            from app.models.project import NovelGenre as _NG
            for g in _NG:
                if g.value == genre or g.name == genre:
                    genre = g
                    break
            else:
                genre = NovelGenre.XUANHUAN
        return self.TEMPLATES.get(genre, self.TEMPLATES[NovelGenre.XUANHUAN])

    def get_style_prompt(self, genre: NovelGenre, style_intensity: str = "standard") -> str:
        """Build a style guidance prompt for the LLM."""
        tmpl = self.get_template(genre)

        intensity_map = {
            "mild": "节奏放缓，更注重氛围和情感描写，爽点适度",
            "standard": "按照标准节奏推进，爽点和过渡交替",
            "intense": "节奏紧凑，爽点密集，高潮频率提高50%",
        }
        intensity_text = intensity_map.get(style_intensity, intensity_map["standard"])

        lines = [
            f"【写作风格指导 — {tmpl['name']}】",
            f"叙事视角: {tmpl['perspective']}",
            f"对话比例: {tmpl['dialogue_ratio']}",
            f"节奏要求: {tmpl['pacing']['excitement_density']}",
            f"高潮/低谷比例: {tmpl['pacing']['low_valley_ratio']}",
            f"强度调节: {intensity_text}",
            f"视角说明: {tmpl['pov_note']}",
            "",
            "【对话风格参考】",
        ]
        for role, style in tmpl["dialogue_style"].items():
            lines.append(f"  - {role}: {style}")

        lines.append("")
        lines.append("【爽点设计】")
        for key, val in tmpl["excitement_design"].items():
            lines.append(f"  - {key}: {val}")

        lines.append("")
        lines.append("【常见元素】")
        lines.append(f"  {', '.join(tmpl['common_tropes'])}")

        return "\n".join(lines)


style_engine = StyleEngine()
