# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Strutture dati condivise."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class Status(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass
class Profile:
    """Segnali pubblici estratti da un profilo."""
    display_name: str = ""
    bio: str = ""
    location: str = ""
    avatar: str = ""
    links: list[str] = field(default_factory=list)
    created_at: str = ""
    avatar_hash: str = ""      # impronta visiva dell'avatar scaricato ("-" = immagine vuota o predefinita)


@dataclass
class SiteResult:
    site: str
    category: str
    username: str
    url: str
    status: Status
    http_status: int | None = None
    profile: Profile = field(default_factory=Profile)
    error: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Evidence:
    """Un singolo motivo per cui due account sembrano della stessa persona."""
    signal: str
    weight: float
    detail: str


@dataclass
class AccountScore:
    site: str
    url: str
    confidence: int  # 0-100
    cluster: int
    evidence: list[Evidence] = field(default_factory=list)
