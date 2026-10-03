"""Measured runtime invariants using isolated stores and injected endpoint faults."""

import argparse
import hashlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from redis import Redis
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vendorlens.config import ModelEndpoint, Settings
from vendorlens.conversation_memory import ConversationMemory
from vendorlens.db import Base
from vendorlens.model_adapter import ModelRateLimitError
from vendorlens.model_routing import HealthRouter
from vendorlens.models import (
    ConversationMessage,
    ConversationSession,
    EpisodicMemory,
    Project,
    utcnow,
)
from vendorlens.skills import SkillManager, SkillReloadError
from vendorlens.strength_eval import timings


def run(output):
    result = {}
    with tempfile.TemporaryDirectory(prefix="vendorlens-metrics-") as temporary:
        root = Path(temporary)
        engine = create_engine(f"sqlite:///{root / 'runtime.sqlite'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        settings = Settings(_env_file=None)
        redis = Redis.from_url(
            "redis://127.0.0.1:6380/0", socket_timeout=2, socket_connect_timeout=2
        )
        try:
            redis.ping()
            redis_available = True
        except Exception:
            redis_available = False
        memory = ConversationMemory(
            settings.model_copy(update={"memory_redis_enabled": redis_available}),
            redis_client=redis if redis_available else None,
        )
        compression = []
        checks = []
        read_latency = []
        keys = []
        ttl_values = []
        with factory() as db:
            db.add(Project(id="metric-project", name="Synthetic isolated benchmark"))
            db.flush()
            for i in range(20):
                conv = ConversationSession(project_id="metric-project", user_id=f"u{i}")
                db.add(conv)
                db.flush()
                start = perf_counter()
                for j in range(16):
                    memory.append(db, conv, "user", f"核实迁移费用与验收条件，第{j}条")
                compression.append((perf_counter() - start) * 1000)
                compressed = db.scalar(
                    select(EpisodicMemory).where(EpisodicMemory.session_id == conv.id)
                )
                uncompressed = list(
                    db.scalars(
                        select(ConversationMessage).where(
                            ConversationMessage.session_id == conv.id,
                            ConversationMessage.compressed.is_(False),
                        )
                    )
                )
                checks.append(
                    bool(compressed)
                    and len(uncompressed) == 6
                    and [m.sequence for m in uncompressed] == list(range(11, 17))
                )
                key = (
                    "vendorlens:working:"
                    + hashlib.sha256(
                        f"{conv.project_id}:{conv.user_id}:{conv.id}".encode()
                    ).hexdigest()
                )
                for _ in range(10):
                    start = perf_counter()
                    memory.recent(db, conv)
                    read_latency.append((perf_counter() - start) * 1000)
                if redis_available:
                    keys.append(key)
                    ttl_values.append(redis.ttl(key))
            db.commit()
        result["memory"] = {
            "sessions": 20,
            "messages": 320,
            "compression_correct": sum(checks),
            "compression_threshold": 16,
            "retained_messages": 6,
            "configured_ttl_seconds": 86400,
            "append_16_messages_latency": timings(compression),
            "recent_read_latency": timings(read_latency),
            "redis_verified": redis_available,
            "observed_ttl_min": min(ttl_values) if ttl_values else None,
            "observed_ttl_max": max(ttl_values) if ttl_values else None,
        }
        if keys:
            redis.delete(*keys)
        rules = root / "skills"
        rules.mkdir()
        file = rules / "rule.json"
        manager = SkillManager(rules)
        reload_latency = []
        good = bad = 0
        for i in range(50):
            payload = {"id": "benchmark-rule", "version": str(i), "content": f"核实第{i}版规则"}
            file.write_text(json.dumps(payload, ensure_ascii=False))
            start = perf_counter()
            manager.reload()
            reload_latency.append((perf_counter() - start) * 1000)
            snapshot = manager.select()[0]
            good += snapshot["version"] == str(i)
            file.write_text("{invalid")
            try:
                manager.reload()
            except SkillReloadError:
                pass
            bad += manager.select()[0]["content_hash"] == snapshot["content_hash"]
        result["skills"] = {
            "valid_updates": 50,
            "valid_update_success": good,
            "invalid_updates": 50,
            "previous_generation_retained": bad,
            "reload_latency": timings(reload_latency),
        }
        passed = []
        switch_latency = []
        for i in range(20):
            now = [utcnow()]
            a = ModelEndpoint(
                id=f"primary-{i}",
                base_url="https://offline.invalid",
                api_key="synthetic",
                model="simulated",
            )
            b = ModelEndpoint(
                id=f"backup-{i}",
                base_url="https://offline.invalid",
                api_key="synthetic",
                model="simulated",
                base_score=95,
            )
            single = HealthRouter(
                factory, Settings(_env_file=None, model_endpoints=[a]), clock=lambda now=now: now[0]
            )
            for _ in range(3):
                _, attempt, _ = single.choose(f"t{i}", "v")
                single.finish(attempt, error=ModelRateLimitError(), requests=[100])
                now[0] += timedelta(seconds=1)
            multi = HealthRouter(
                factory,
                Settings(_env_file=None, model_endpoints=[a, b]),
                clock=lambda now=now: now[0],
            )
            start = perf_counter()
            chosen, attempt, _ = multi.choose(f"t{i}", "v")
            switch_latency.append((perf_counter() - start) * 1000)
            backup_ok = chosen.id == b.id
            multi.finish(attempt, requests=[50])
            now[0] += timedelta(seconds=61)
            chosen, attempt, decision = multi.choose(f"t{i}", "v")
            probe_ok = chosen.id == a.id and decision["probe"]
            other, other_attempt, _ = multi.choose(f"t{i}", "other")
            exclusive = other.id == b.id
            multi.finish(other_attempt, requests=[50])
            multi.finish(attempt, requests=[50])
            restored, new_attempt, _ = multi.choose(f"t{i}", "v")
            multi.finish(new_attempt, requests=[50])
            passed.append(backup_ok and probe_ok and exclusive and restored.id == a.id)
        result["routing"] = {
            "scenarios": 20,
            "passed": sum(passed),
            "injected_rate_limits": 60,
            "mode": "simulated faults, real HealthRouter and SQLite, no remote endpoint calls",
            "switch_decision_latency": timings(switch_latency),
        }
        engine.dispose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
