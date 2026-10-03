import json

import pytest
from pydantic import ValidationError
from test_model_analyzer import review_input

from vendorlens.model_analyzer import VendorReviewModelAnalyzer
from vendorlens.review_agent import VendorReviewInput
from vendorlens.schemas import ReviewDomain
from vendorlens.skills import SkillManager, SkillReloadError


def write_skill(root, **changes):
    root.mkdir(exist_ok=True)
    payload = {
        "id": "software",
        "version": "1",
        "industries": ["software"],
        "domains": ["cost_modeling"],
        "content": "核验维保费用",
        **changes,
    }
    (root / "policy.json").write_text(json.dumps(payload), encoding="utf-8")


def test_selection_hot_reload_disable_and_invalid_file_retains_previous(tmp_path):
    write_skill(tmp_path)
    manager = SkillManager(tmp_path)
    assert not manager.select("hardware")
    assert not manager.select("software", domains=[ReviewDomain.REQUIREMENT_COMPLIANCE])
    frozen = manager.select("software")
    write_skill(tmp_path, version="2", content="核验付款条件")
    assert manager.select("software")[0]["version"] == "2"
    assert frozen[0]["content"] == "核验维保费用"
    (tmp_path / "broken.json").write_text("{bad")
    with pytest.raises(SkillReloadError):
        manager.reload()
    assert manager.select("software")[0]["version"] == "2"
    (tmp_path / "broken.json").unlink()
    write_skill(tmp_path, enabled=False)
    assert not manager.select("software")


def test_frozen_rules_enter_system_prompt_and_hash_tamper_is_rejected(tmp_path):
    write_skill(tmp_path)
    snapshots = SkillManager(tmp_path).select("software")
    data = review_input().model_copy(
        update={"skill_snapshots": snapshots, "memory_context": {"episodes": []}}
    )
    messages = VendorReviewModelAnalyzer._messages(data)
    assert "核验维保费用" in messages[0]["content"]
    assert "conversation_context" in messages[1]["content"]
    snapshots[0]["content"] = "伪造规则"
    with pytest.raises(ValidationError, match="hash mismatch"):
        VendorReviewInput.model_validate(data.model_dump())


def test_markdown_front_matter_and_duplicate_id_are_validated(tmp_path):
    root = tmp_path / "md"
    root.mkdir()
    (root / "SKILL.md").write_text('---\n{"id":"policy","version":"1"}\n---\n核验事实')
    manager = SkillManager(tmp_path)
    assert manager.select()[0]["content"] == "核验事实"
    write_skill(tmp_path, id="policy")
    with pytest.raises(SkillReloadError):
        manager.reload()
