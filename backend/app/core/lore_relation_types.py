"""Author-facing relation types with stable internal identities."""

import hashlib
import re


RELATION_TYPES: dict[str, dict[str, object]] = {
    "related_to": {
        "display_name": "一般关联",
        "forward_label": "关联于",
        "reverse_label": "关联于",
        "symmetric": True,
    },
    "ally": {
        "display_name": "盟友",
        "forward_label": "盟友",
        "reverse_label": "盟友",
        "symmetric": True,
    },
    "enemy": {
        "display_name": "敌对",
        "forward_label": "与其敌对",
        "reverse_label": "与其敌对",
        "symmetric": True,
    },
    "member_of": {
        "display_name": "隶属",
        "forward_label": "隶属于",
        "reverse_label": "成员包括",
        "symmetric": False,
    },
    "located_in": {
        "display_name": "地点",
        "forward_label": "位于",
        "reverse_label": "包含",
        "symmetric": False,
    },
    "owns": {
        "display_name": "持有",
        "forward_label": "持有",
        "reverse_label": "持有者是",
        "symmetric": False,
    },
    "participates_in": {
        "display_name": "参与",
        "forward_label": "参与",
        "reverse_label": "参与者包括",
        "symmetric": False,
    },
    "causes": {
        "display_name": "因果",
        "forward_label": "导致",
        "reverse_label": "由其导致",
        "symmetric": False,
    },
    "affects": {
        "display_name": "影响",
        "forward_label": "影响",
        "reverse_label": "受其影响",
        "symmetric": False,
    },
}


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def resolve_relation_type(
    relation_type: str,
    custom_forward_label: str | None,
    custom_reverse_label: str | None,
) -> tuple[str, str, str, bool]:
    """Return internal key, labels, and symmetric flag for a user choice."""
    if relation_type == "custom":
        forward = _normalized_label(custom_forward_label or "")
        reverse = _normalized_label(custom_reverse_label or "")
        if not forward or not reverse:
            raise ValueError("自定义关系需要填写两个方向的说法")
        if len(forward) > 100 or len(reverse) > 100:
            raise ValueError("关系说法不能超过 100 个字符")
        digest = hashlib.sha256(f"{forward}\n{reverse}".encode("utf-8")).hexdigest()
        return f"custom:{digest[:24]}", forward, reverse, False

    definition = RELATION_TYPES.get(relation_type)
    if definition is None:
        raise ValueError("关系类型无效")
    return (
        relation_type,
        str(definition["forward_label"]),
        str(definition["reverse_label"]),
        bool(definition["symmetric"]),
    )
