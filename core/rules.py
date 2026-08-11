"""Download Rules & Automation.

Rules automatically configure new downloads before they reach the queue:

    *.mp4  →  Videos  →  D:\\Downloads\\Videos
    *.pdf  →  Documents
    example.com  →  D:\\Downloads\\Example

Each rule has a set of AND'ed conditions and a set of actions.  The highest
priority matching rule wins; ties break deterministically by creation order.
Rules only *fill in* values the user has not explicitly chosen — an explicit
user selection always overrides an automatic rule.

Rules are pure data: they never execute code.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger("n13")

# Condition fields
EXTENSION = "extension"
DOMAIN = "domain"
URL_CONTAINS = "url_contains"
FILENAME_CONTAINS = "filename_contains"
MIME = "mime"
MIN_SIZE = "min_size"
MAX_SIZE = "max_size"

_CONDITION_FIELDS = (
    EXTENSION, DOMAIN, URL_CONTAINS, FILENAME_CONTAINS, MIME, MIN_SIZE, MAX_SIZE,
)


@dataclass
class RuleCondition:
    field: str
    value: str = ""

    def matches(self, url: str, filename: str, size: int, content_type: str) -> bool:
        val = (self.value or "").strip().lower()
        if not val:
            return True
        if self.field == EXTENSION:
            ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
            return ext == val.lstrip(".")
        if self.field == DOMAIN:
            host = (urlparse(url or "").hostname or "").lower()
            return val == host or host.endswith("." + val)
        if self.field == URL_CONTAINS:
            return val in (url or "").lower()
        if self.field == FILENAME_CONTAINS:
            return val in (filename or "").lower()
        if self.field == MIME:
            return val in (content_type or "").lower()
        if self.field == MIN_SIZE:
            try:
                return size >= int(val)
            except (TypeError, ValueError):
                return False
        if self.field == MAX_SIZE:
            try:
                return size <= int(val)
            except (TypeError, ValueError):
                return False
        return True


@dataclass
class DownloadRule:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = "Untitled rule"
    enabled: bool = True
    priority: int = 0
    conditions: List[RuleCondition] = field(default_factory=list)
    category: str = ""
    folder: str = ""
    priority_value: int = 5
    connection_mode: str = ""          # "" | "smart" | "manual"
    manual_connections: int = 0        # used when connection_mode == "manual"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def matches(self, url: str, filename: str, size: int, content_type: str) -> bool:
        return all(c.matches(url, filename, size, content_type) for c in self.conditions)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["conditions"] = [asdict(c) for c in self.conditions]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadRule":
        data = dict(data)
        conds = data.pop("conditions", []) or []
        rule = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        parsed = []
        for c in conds:
            if isinstance(c, dict) and c.get("field") in _CONDITION_FIELDS:
                parsed.append(RuleCondition(field=c["field"], value=str(c.get("value", ""))))
        rule.conditions = parsed
        return rule


class RuleEngine:
    """Persistent store + matcher for download rules."""

    def __init__(self, rules_path: Optional[Path] = None):
        self._path = Path(rules_path) if rules_path else None
        self._rules: List[DownloadRule] = []
        if self._path is not None:
            self.load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._rules = [DownloadRule.from_dict(r) for r in data if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not load download rules: %s", exc)

    def save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(
                json.dumps([r.to_dict() for r in self._rules], indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            log.warning("Could not save download rules: %s", exc)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def all(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._rules]

    def add(self, rule: DownloadRule) -> str:
        self._rules.append(rule)
        self.save()
        return rule.id

    def update(self, rule_id: str, fields: Dict[str, Any]) -> bool:
        for r in self._rules:
            if r.id == rule_id:
                if "conditions" in fields:
                    conds = []
                    for c in fields["conditions"] or []:
                        if isinstance(c, dict) and c.get("field") in _CONDITION_FIELDS:
                            conds.append(RuleCondition(field=c["field"], value=str(c.get("value", ""))))
                    r.conditions = conds
                for key in ("name", "enabled", "priority", "category", "folder",
                            "priority_value", "connection_mode", "manual_connections"):
                    if key in fields:
                        setattr(r, key, fields[key])
                r.updated_at = time.time()
                self.save()
                return True
        return False

    def delete(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.id != rule_id]
        if len(self._rules) != before:
            self.save()
            return True
        return False

    def duplicate(self, rule_id: str) -> Optional[str]:
        for r in self._rules:
            if r.id == rule_id:
                copy = DownloadRule.from_dict(r.to_dict())
                copy.id = uuid.uuid4().hex[:10]
                copy.name = r.name + " (copy)"
                copy.created_at = time.time()
                copy.updated_at = time.time()
                self._rules.append(copy)
                self.save()
                return copy.id
        return None

    def reorder(self, rule_ids: List[str]) -> None:
        by_id = {r.id: r for r in self._rules}
        ordered = [by_id[i] for i in rule_ids if i in by_id]
        for r in self._rules:
            if r.id not in {x.id for x in ordered}:
                ordered.append(r)
        self._rules = ordered
        self.save()

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    def evaluate(self, url: str, filename: str, size: int = 0,
                 content_type: str = "") -> Optional[DownloadRule]:
        """Best matching enabled rule (highest priority; tie → oldest first)."""
        enabled = [r for r in self._rules if r.enabled and r.matches(url, filename, size, content_type)]
        if not enabled:
            return None
        # Deterministic: higher priority wins; equal priority → oldest created.
        enabled.sort(key=lambda r: (-r.priority, r.created_at, r.id))
        return enabled[0]
