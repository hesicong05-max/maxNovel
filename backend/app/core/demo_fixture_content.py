"""Frozen, side-effect-free content graph for technical demo fixture v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TypeSpec:
    key: str
    display_name: str
    fields: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ElementSpec:
    slug: str
    type_key: str
    name: str
    summary: str
    payload: dict[str, Any]
    excerpt: str
    source_section: str


@dataclass(frozen=True)
class ChapterSpec:
    slug: str
    title: str
    summary: str
    position: int


@dataclass(frozen=True)
class AssignmentSpec:
    slug: str
    element_slug: str
    scope_type: Literal["novel", "part", "chapter"]


@dataclass(frozen=True)
class RelationSpec:
    slug: str
    source_slug: str
    target_slug: str
    relation_key: str
    forward_label: str
    reverse_label: str
    description: str


PROJECT_TITLE = "雾港回声（技术模拟样例）"
PART_TITLE = "雾潮初临"
PART_DESCRIPTION = (
    "雾潮逼近，星港异常停摆。沈星先检查褪色航标，再在低潮窗口核对旧航路。"
)
SOURCE_REFERENCE = "《雾港回声》非生产固定样例原稿 v1"

# Literal copies of the platform's built-in schemas at fixture-v1 freeze time.
# Do not import the live mapping here: future platform schema changes must not
# silently rewrite the meaning of an already shared fixture version.
TYPE_FIELD_SCHEMA_SNAPSHOTS: dict[str, tuple[dict[str, Any], ...]] = {
    "character": (
        {
            "key": "identity",
            "label": "身份",
            "control": "text",
            "value_type": "string",
            "help": "角色的身份标识",
            "order": 5,
            "required": False,
        },
        {
            "key": "appearance",
            "label": "外貌",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的外貌描述",
            "order": 10,
            "required": False,
        },
        {
            "key": "personality",
            "label": "性格",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的性格特点",
            "order": 20,
            "required": False,
        },
        {
            "key": "background",
            "label": "背景",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的背景故事",
            "order": 30,
            "required": False,
        },
        {
            "key": "abilities",
            "label": "能力",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的能力和特长",
            "order": 40,
            "required": False,
        },
        {
            "key": "limitations",
            "label": "限制",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的弱点和限制",
            "order": 50,
            "required": False,
        },
        {
            "key": "goals",
            "label": "目标",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的目标",
            "order": 60,
            "required": False,
        },
        {
            "key": "motivations",
            "label": "动机",
            "control": "textarea",
            "value_type": "text",
            "help": "角色的内在动机",
            "order": 70,
            "required": False,
        },
        {
            "key": "possible_plots",
            "label": "可能剧情",
            "control": "textarea",
            "value_type": "text",
            "help": "角色可能参与的剧情线索",
            "order": 80,
            "required": False,
        },
    ),
    "location": (
        {
            "key": "description",
            "label": "描述",
            "control": "textarea",
            "value_type": "text",
            "help": "地点的具体描述",
            "order": 10,
            "required": False,
        },
        {
            "key": "significance",
            "label": "重要性",
            "control": "textarea",
            "value_type": "text",
            "help": "地点在故事中的重要性",
            "order": 20,
            "required": False,
        },
        {
            "key": "geography",
            "label": "地理特征",
            "control": "textarea",
            "value_type": "text",
            "help": "地点的地理特征和环境",
            "order": 30,
            "required": False,
        },
    ),
    "faction": (
        {
            "key": "stance",
            "label": "立场",
            "control": "textarea",
            "value_type": "text",
            "help": "阵营的立场和理念",
            "order": 10,
            "required": False,
        },
        {
            "key": "power_level",
            "label": "实力",
            "control": "text",
            "value_type": "string",
            "help": "阵营的实力等级",
            "order": 20,
            "required": False,
        },
        {
            "key": "goal",
            "label": "目标",
            "control": "textarea",
            "value_type": "text",
            "help": "阵营的目标",
            "order": 30,
            "required": False,
        },
        {
            "key": "structure",
            "label": "组织结构",
            "control": "textarea",
            "value_type": "text",
            "help": "阵营的组织结构",
            "order": 40,
            "required": False,
        },
    ),
    "item": (
        {
            "key": "description",
            "label": "描述",
            "control": "textarea",
            "value_type": "text",
            "help": "物品的具体描述",
            "order": 10,
            "required": False,
        },
        {
            "key": "origin",
            "label": "来源",
            "control": "textarea",
            "value_type": "text",
            "help": "物品的来源与创造",
            "order": 20,
            "required": False,
        },
        {
            "key": "power",
            "label": "能力/效果",
            "control": "textarea",
            "value_type": "text",
            "help": "物品的能力或特殊效果",
            "order": 30,
            "required": False,
        },
        {
            "key": "limitations",
            "label": "限制",
            "control": "textarea",
            "value_type": "text",
            "help": "物品的使用限制",
            "order": 40,
            "required": False,
        },
    ),
    "rule": (
        {
            "key": "levels",
            "label": "层级",
            "control": "textarea",
            "value_type": "text",
            "help": "规则体系的层级",
            "order": 10,
            "required": False,
        },
        {
            "key": "rules",
            "label": "规则",
            "control": "textarea",
            "value_type": "text",
            "help": "具体的规则内容",
            "order": 20,
            "required": False,
        },
        {
            "key": "limitations",
            "label": "限制",
            "control": "textarea",
            "value_type": "text",
            "help": "规则的限制条件",
            "order": 30,
            "required": False,
        },
        {
            "key": "description",
            "label": "描述",
            "control": "textarea",
            "value_type": "text",
            "help": "规则的详细描述",
            "order": 40,
            "required": False,
        },
        {
            "key": "scope",
            "label": "适用范围",
            "control": "textarea",
            "value_type": "text",
            "help": "规则适用的范围",
            "order": 50,
            "required": False,
        },
    ),
    "foreshadow": (
        {
            "key": "description",
            "label": "描述",
            "control": "textarea",
            "value_type": "text",
            "help": "伏笔的具体描述",
            "order": 10,
            "required": False,
        },
        {
            "key": "hint",
            "label": "线索提示",
            "control": "textarea",
            "value_type": "text",
            "help": "埋入章节中的线索提示",
            "order": 50,
            "required": False,
        },
    ),
}

TYPE_SPECS = tuple(
    TypeSpec(key, display_name, TYPE_FIELD_SCHEMA_SNAPSHOTS[key])
    for key, display_name in (
        ("character", "角色"),
        ("location", "地点"),
        ("faction", "阵营"),
        ("item", "物品"),
        ("rule", "规则与限制"),
        ("foreshadow", "伏笔"),
    )
)

ELEMENT_SPECS = (
    ElementSpec(
        "shen_xing",
        "character",
        "沈星",
        "守夜司调查员，负责查明星港停摆与航标异常。",
        {
            "identity": "守夜司调查员",
            "personality": "谨慎，善于观察",
            "background": "受命调查星港停摆事件",
            "abilities": "能够辨认航标脉冲和潮位记录",
            "limitations": "本人不能越过潮汐门限，必须依赖合规航道与校准设备",
            "goals": "查明停摆原因并核对褪色航标",
            "motivations": "保护港区居民并完成守夜司委托",
            "possible_plots": "第一章检查航标，第二章在低潮窗口解读旧航路坐标",
        },
        (
            "沈星是守夜司调查员，性格谨慎、善于观察导航脉冲。她受命调查星港停摆，"
            "但本人不能越过潮汐门限；她的目标是在不解除禁航令的前提下确认褪色航标"
            "是否记录了旧航路。"
        ),
        "角色：沈星",
    ),
    ElementSpec(
        "star_harbor",
        "location",
        "星港",
        "建在雾海岛礁上的中转港，外环航道因异常停摆。",
        {
            "description": "雾海岛礁上的中转港",
            "significance": "故事调查现场，也是旧航路入口",
            "geography": "内港静水区、外环潮线、中央航标塔与废弃西栈桥",
        },
        (
            "星港由内港静水区、外环潮线和中央航标塔组成。异常发生后，外环航标失去"
            "同步，所有对外航道暂停；港内居民仍可在静水区活动。"
        ),
        "地点：星港",
    ),
    ElementSpec(
        "night_watch",
        "faction",
        "守夜司",
        "负责星港夜航安全、航标维护和临时禁航审查的机构。",
        {
            "stance": "先保障航行安全，再核查停摆原因",
            "power_level": "拥有港区航标和临时航道管制权",
            "goal": "确认异常来源并安全恢复航道",
            "structure": "夜值长、调查员、航标维护员",
        },
        (
            "守夜司负责维护夜航秩序和航标记录。停摆后，司内先发布临时禁航令，再派"
            "沈星调查；在证据明确前，不得擅自开放外环航道。"
        ),
        "阵营：守夜司",
    ),
    ElementSpec(
        "star_key",
        "item",
        "星钥",
        "守夜司保管的黄铜校准器，可读取并校准旧式航标脉冲。",
        {
            "description": "黄铜航标校准器",
            "origin": "由守夜司航标维护组保管",
            "power": "读取旧式航标脉冲并在低潮窗口完成校准",
            "limitations": "不能替代许可，不能单独打开航道，非低潮时无效",
        },
        (
            "星钥是一枚黄铜校准器，靠近旧式航标时会显示脉冲方向。它只能在低潮窗口"
            "校准航标，不能替代通行许可，也不能让持有者直接越过潮汐门限。"
        ),
        "物品：星钥",
    ),
    ElementSpec(
        "tidal_threshold",
        "rule",
        "潮汐门限",
        "雾潮期间控制星港外环通行的航道规则。",
        {
            "levels": "内港静水区 / 外环潮线 / 封闭航道",
            "rules": "低潮窗口、有效航标、登记航线必须同时满足",
            "limitations": "星钥只能校准航标，不能单独赋予通行权",
            "description": "星港外环航行安全规则",
            "scope": "星港外环及旧航路入口",
        },
        (
            "潮汐门限把港区分为内港静水区、外环潮线和封闭航道。只有低潮窗口、有"
            "效航标和登记航线同时满足时，船只才可越过外环潮线；任何单一工具都不能"
            "绕过该规则。"
        ),
        "规则：潮汐门限",
    ),
    ElementSpec(
        "navigation_ban",
        "rule",
        "禁航令",
        "停摆后生效的临时港规，禁止船只进入外环航道。",
        {
            "levels": "临时港规",
            "rules": "普通船只不得进入外环；调查仅限检修区",
            "limitations": "只在停摆调查期间生效，解除需航标恢复与守夜司复核",
            "description": "防止船只误入失步航道的临时措施",
            "scope": "星港外环航道",
        },
        (
            "禁航令由守夜司在外环航标失步后发布。禁航期间，普通船只不得越过外环"
            "潮线；调查员可在低潮窗口进入航标检修区，但不能开启对外航道。"
        ),
        "规则：禁航令",
    ),
    ElementSpec(
        "faded_beacon",
        "foreshadow",
        "褪色航标",
        "废弃西栈桥的旧航标，第三道刻痕可能藏有旧航路坐标。",
        {
            "description": "废弃西栈桥的旧式航标，第三道刻痕可能保存旧航路坐标",
            "hint": "第一章只出现逆向微光与第三道刻痕，不解释用途",
        },
        (
            "废弃西栈桥立着一座褪色航标。它的第三道刻痕在星钥靠近时会出现逆向"
            "微光，但原稿尚未说明其用途；守夜司旧档案只记载它曾服务于禁航令发布"
            "前的航路。"
        ),
        "伏笔：褪色航标",
    ),
)

CHAPTER_SPECS = (
    ChapterSpec(
        "chapter_one",
        "停摆的星港",
        (
            "沈星抵达停摆的星港，携星钥检查废弃西栈桥的褪色航标；她只观察到第三道"
            "刻痕的逆向微光，尚未解释其用途。"
        ),
        1,
    ),
    ChapterSpec(
        "chapter_two",
        "潮线之外",
        (
            "低潮窗口到来后，沈星在守夜司许可范围内校准褪色航标，计划核对旧航路"
            "坐标与禁航令发布原因。"
        ),
        2,
    ),
)

ASSIGNMENT_SPECS = (
    AssignmentSpec("star_harbor_novel", "star_harbor", "novel"),
    AssignmentSpec("tidal_threshold_novel", "tidal_threshold", "novel"),
    AssignmentSpec("navigation_ban_novel", "navigation_ban", "novel"),
    AssignmentSpec("night_watch_part", "night_watch", "part"),
    AssignmentSpec("faded_beacon_part", "faded_beacon", "part"),
    AssignmentSpec("shen_xing_chapter", "shen_xing", "chapter"),
    AssignmentSpec("star_key_chapter", "star_key", "chapter"),
)

RELATION_SPECS = (
    RelationSpec(
        "shen_xing_serves_night_watch",
        "shen_xing",
        "night_watch",
        "member_of",
        "隶属于",
        "成员包括",
        "沈星以调查员身份接受守夜司的星港停摆调查任务。",
    ),
    RelationSpec(
        "star_key_calibrates_under_threshold",
        "star_key",
        "tidal_threshold",
        "custom:860ff8ddfb6b7df47bf53c06",
        "可在规则内校准",
        "限制其使用",
        "星钥只在潮汐门限规定的低潮窗口校准航标，不能绕过通行条件。",
    ),
    RelationSpec(
        "faded_beacon_located_at_harbor",
        "faded_beacon",
        "star_harbor",
        "located_in",
        "位于",
        "包含",
        "褪色航标位于星港废弃西栈桥。",
    ),
)

FORESHADOW_PLANT_NOTE = (
    "在沈星检查航标时展示第三道刻痕与逆向微光，不解释用途。此项仅为未来写作计划。"
)
FORESHADOW_RESOLVE_CONDITION = "低潮窗口出现，沈星已取得星钥并完成航标校准。"
FORESHADOW_RESOLVE_NOTE = (
    "揭示刻痕记录的是禁航令发布前的旧航路坐标，并说明守夜司封锁外环的安全原因。"
    "此项仅为未来写作计划。"
)

TYPE_BY_KEY = {spec.key: spec for spec in TYPE_SPECS}
ELEMENT_BY_SLUG = {spec.slug: spec for spec in ELEMENT_SPECS}
CHAPTER_BY_SLUG = {spec.slug: spec for spec in CHAPTER_SPECS}
