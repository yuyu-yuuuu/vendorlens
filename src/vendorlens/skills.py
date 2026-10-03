"""Validated, atomic hot reload. Tasks persist selected content, not live file references."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendorlens.schemas import ReviewDomain


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    version: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    industries: list[str] = Field(default_factory=lambda: ["general"])
    domains: list[ReviewDomain] = Field(default_factory=lambda: list(ReviewDomain))
    keywords: list[str] = Field(default_factory=list)
    content: str = Field(min_length=1, max_length=20000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("skill content must not be blank")
        return value

    def snapshot(self):
        payload = self.model_dump(mode="json")
        payload["content_hash"] = hashlib.sha256(self.content.encode()).hexdigest()
        return payload


class SkillReloadError(ValueError):
    pass


class SkillManager:
    def __init__(self, root: Path):
        self.root = root
        self._lock = RLock()
        self._skills = ()
        self._fingerprint = None
        self.last_error = None

    def reload(self, *, force=True):
        with self._lock:
            files = sorted(self.root.rglob("*.json")) + sorted(self.root.rglob("SKILL.md"))
            try:
                contents = [(path, path.read_bytes()) for path in files]
                fingerprint = hashlib.sha256(
                    b"".join(str(p).encode() + b + b"\0" for p, b in contents)
                ).hexdigest()
                if not force and fingerprint == self._fingerprint:
                    return self.summary()
                parsed = []
                for path, raw in contents:
                    if path.suffix == ".md":
                        text = raw.decode("utf-8")
                        if not text.startswith("---\n"):
                            raise ValueError("Markdown skills need JSON front matter")
                        header, body = text[4:].split("\n---\n", 1)
                        payload = {**json.loads(header), "content": body.strip()}
                    else:
                        payload = json.loads(raw)
                    parsed.append(Skill.model_validate(payload))
                if len({s.id for s in parsed}) != len(parsed):
                    raise ValueError("duplicate skill id")
                # Replace only after every file passes; no partial update.
                self._skills = tuple(parsed)
                self._fingerprint = fingerprint
                self.last_error = None
            except (OSError, UnicodeError, ValueError) as error:
                self.last_error = type(error).__name__
                raise SkillReloadError("invalid skill files; previous rules retained") from None
            return self.summary()

    def select(self, industry="general", message="", domains=None):
        try:
            self.reload(force=False)
        except SkillReloadError:
            if self._fingerprint is None:
                raise
        wanted = set(domains or ReviewDomain)
        with self._lock:
            return [
                s.snapshot()
                for s in self._skills
                if s.enabled
                and ("general" in s.industries or industry in s.industries)
                and wanted.intersection(s.domains)
                and (not s.keywords or any(k.lower() in message.lower() for k in s.keywords))
            ]

    def summary(self):
        with self._lock:
            return {
                "generation": self._fingerprint,
                "last_error": self.last_error,
                "skills": [
                    {k: v for k, v in s.snapshot().items() if k != "content"} for s in self._skills
                ],
            }


@lru_cache(maxsize=8)
def skill_manager(root: Path):
    return SkillManager(root)


def validate_skill_snapshots(snapshots):
    for snapshot in snapshots:
        payload = dict(snapshot)
        expected = payload.pop("content_hash")
        skill = Skill.model_validate(payload)
        if skill.snapshot()["content_hash"] != expected:
            raise ValueError("skill snapshot hash mismatch")
    return snapshots
