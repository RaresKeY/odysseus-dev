"""Durable, test-only aggregate accounting at the canonical provider boundary.

No credentials, transport, retry, reset or billing discovery lives here. Every
admitted version shares one immutable campaign and one SQLite database. A lost
owner leaves a held reservation; a later owner cannot infer that it was free.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from .contracts import DecisionError, decode, encode

CAMPAIGN_ID = "odysseus-jev-extended-20261024-v1"
HARD_LIMIT = 5_000_000
MODEL = "jev-1.14.0"
URL = "https://api.typesafe.ai/v1/systemone"
STAGES = {"smoke": (2, 150_000), "small": (24, 350_000),
          "development": (120, 1_000_000), "heldout": (192, 1_500_000),
          "confirmation": (96, 1_000_000)}


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value)).hexdigest()


@dataclass(frozen=True)
class BillingContract:
    """Numerical service bounds require independently reviewed evidence.

    A finite JSON shape or HTTP read limit does not establish generated-token
    or branch-billing limits. The supplied public reference currently leaves
    that numerical output/branch bound unproved; live admission stays closed.
    Offline tests can inject a synthetic service contract.
    """
    input_tokens: int
    output_tokens: int
    evidence_sha256: str
    verified: bool = False

    def checked(self):
        if (type(self.input_tokens) is not int or type(self.output_tokens) is not int
                or min(self.input_tokens, self.output_tokens) < 1
                or self.input_tokens + self.output_tokens > HARD_LIMIT
                or len(self.evidence_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.evidence_sha256)):
            raise DecisionError("campaign_invalid_billing_contract")
        return self


class CampaignLedger:
    """BEGIN IMMEDIATE + FULL synchronous owner, safe across threads/processes.

    `authorization` and canonical path are immutable. Versions are append-only;
    explicit source/suite/policy changes never create another token allowance.
    """

    def __init__(self, path, *, authorization: dict):
        self.path = Path(path).absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.path.parent.lstat()
        if (self.path.parent.resolve() != self.path.parent or not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700):
            raise DecisionError("campaign_unsafe_ledger_directory")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise DecisionError("campaign_unsafe_ledger")
            os.fsync(fd)
        finally:
            os.close(fd)
        self.authorization = {"campaign_id": CAMPAIGN_ID, "limit": HARD_LIMIT,
                              "ledger_path": str(self.path), "authorization": authorization,
                              "stages": {key: list(value) for key, value in STAGES.items()}}
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS campaign (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL,
                    halted INTEGER NOT NULL DEFAULT 0, halt_reason TEXT);
                CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, identity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gates (stage TEXT PRIMARY KEY, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, case_key TEXT UNIQUE NOT NULL,
                    version TEXT NOT NULL REFERENCES versions(id), stage TEXT NOT NULL,
                    binding TEXT NOT NULL, status TEXT NOT NULL, input_bound INTEGER NOT NULL,
                    output_bound INTEGER NOT NULL, input_tokens TEXT, output_tokens TEXT,
                    charge TEXT NOT NULL, response_sha256 TEXT, response_payload_sha256 TEXT, outcome TEXT, failure_kind TEXT,
                    created REAL NOT NULL, finished REAL);
                CREATE TRIGGER IF NOT EXISTS campaign_identity_immutable BEFORE UPDATE OF identity ON campaign
                    BEGIN SELECT RAISE(ABORT, 'immutable campaign'); END;
                CREATE TRIGGER IF NOT EXISTS campaign_no_delete BEFORE DELETE ON campaign
                    BEGIN SELECT RAISE(ABORT, 'immutable campaign'); END;
                CREATE TRIGGER IF NOT EXISTS versions_no_update BEFORE UPDATE ON versions
                    BEGIN SELECT RAISE(ABORT, 'append only versions'); END;
                CREATE TRIGGER IF NOT EXISTS versions_no_delete BEFORE DELETE ON versions
                    BEGIN SELECT RAISE(ABORT, 'append only versions'); END;
                CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
                    BEGIN SELECT RAISE(ABORT, 'sealed attempts'); END;
                CREATE TRIGGER IF NOT EXISTS attempt_identity_immutable BEFORE UPDATE OF
                    id,case_key,version,stage,binding,input_bound,output_bound,created ON attempts
                    BEGIN SELECT RAISE(ABORT, 'sealed attempt identity'); END;
            """)
            # executescript commits its preamble; acquire again for identity admission.
            db.execute("BEGIN IMMEDIATE")
            identity = encode(self.authorization).decode()
            previous = db.execute("SELECT identity FROM campaign WHERE id=1").fetchone()
            if previous is None:
                db.execute("INSERT INTO campaign(id,identity) VALUES (1,?)", (identity,))
            elif previous[0] != identity:
                raise DecisionError("campaign_identity_mismatch")
        self._fsync_directory()

    def _fsync_directory(self):
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def admit_version(self, identity):
        if (not isinstance(identity, dict) or set(identity) != {"source", "suite", "policy", "config", "contract"}
                or any(not isinstance(v, str) or not v or len(v) > 256 for v in identity.values())):
            raise DecisionError("campaign_invalid_version")
        version = digest(identity)
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO versions(id,identity) VALUES (?,?)", (version, encode(identity).decode()))
        return version

    def advance(self, stage, *, evidence):
        """Explicit reviewed gate, never automatic spend-to-quota progression."""
        if stage not in STAGES or not isinstance(evidence, str) or not evidence or len(evidence) > 256:
            raise DecisionError("campaign_invalid_stage_gate")
        with self._transaction() as db:
            self._available(db)
            earlier = list(STAGES)[:list(STAGES).index(stage)]
            if any(db.execute("SELECT 1 FROM gates WHERE stage=?", (x,)).fetchone() is None for x in earlier):
                raise DecisionError("campaign_stage_order")
            prior = db.execute("SELECT evidence FROM gates WHERE stage=?", (stage,)).fetchone()
            if prior is not None and prior[0] != evidence:
                raise DecisionError("campaign_stage_gate_mismatch")
            db.execute("INSERT OR IGNORE INTO gates VALUES (?,?)", (stage, evidence))

    @staticmethod
    def _available(db):
        if db.execute("SELECT halted FROM campaign WHERE id=1").fetchone()[0]:
            raise DecisionError("campaign_halted")
        if db.execute("SELECT 1 FROM attempts WHERE status IN ('reserved','dispatching','known','unresolved') LIMIT 1").fetchone():
            raise DecisionError("campaign_unresolved_attempt")

    def reserve(self, *, attempt_id, case_key, version, stage, binding, contract):
        contract.checked()
        if not contract.verified:
            raise DecisionError("campaign_billing_bound_unverified")
        if stage not in STAGES or not isinstance(case_key, str) or not case_key or len(case_key) > 256:
            raise DecisionError("campaign_invalid_attempt")
        charge = contract.input_tokens + contract.output_tokens
        with self._transaction() as db:
            self._available(db)
            if db.execute("SELECT 1 FROM gates WHERE stage=?", (stage,)).fetchone() is None:
                raise DecisionError("campaign_stage_not_admitted")
            if db.execute("SELECT 1 FROM versions WHERE id=?", (version,)).fetchone() is None:
                raise DecisionError("campaign_version_not_admitted")
            charges = db.execute("SELECT stage,charge FROM attempts").fetchall()
            calls = sum(x["stage"] == stage for x in charges)
            used = sum(int(x["charge"]) for x in charges if x["stage"] == stage)
            total = sum(int(x["charge"]) for x in charges)
            if calls >= STAGES[stage][0] or used + charge > STAGES[stage][1] or total + charge > HARD_LIMIT:
                raise DecisionError("campaign_budget_unavailable")
            if db.execute("SELECT 1 FROM attempts WHERE id=? OR case_key=?", (attempt_id, case_key)).fetchone():
                raise DecisionError("campaign_attempt_already_exists")
            db.execute("""INSERT INTO attempts(id,case_key,version,stage,binding,status,input_bound,output_bound,charge,created)
                VALUES (?,?,?,?,?,'reserved',?,?,?,?)""", (attempt_id, case_key, version, stage,
                encode(binding).decode(), contract.input_tokens, contract.output_tokens, str(charge), time.time()))

    def dispatch(self, attempt_id, binding):
        with self._transaction() as db:
            row = self._attempt(db, attempt_id)
            if db.execute("SELECT halted FROM campaign WHERE id=1").fetchone()[0]:
                raise DecisionError("campaign_halted")
            if row["status"] != "reserved" or row["binding"] != encode(binding).decode():
                raise DecisionError("campaign_dispatch_identity_mismatch")
            db.execute("UPDATE attempts SET status='dispatching' WHERE id=?", (attempt_id,))

    @staticmethod
    def _attempt(db, attempt_id):
        row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise DecisionError("campaign_attempt_missing")
        return {**dict(row), **{key: int(row[key]) if row[key] is not None else None
                for key in ("charge", "input_tokens", "output_tokens")}}

    def response(self, attempt_id, usage, response_sha256, response_payload_sha256=None):
        """Commit independently known usage before decision interpretation."""
        known = {key: usage.get(key) if isinstance(usage, dict) and type(usage.get(key)) is int
                 and usage[key] >= 0 else None
                 for key in ("input_tokens", "output_tokens")}
        overrun = False
        with self._transaction() as db:
            row = self._attempt(db, attempt_id)
            if row["status"] != "dispatching":
                raise DecisionError("campaign_response_already_recorded")
            complete = all(value is not None for value in known.values())
            overrun = any(known[key] is not None and known[key] > row[bound] for key, bound in
                          (("input_tokens", "input_bound"), ("output_tokens", "output_bound")))
            # Retain the whole conservative reservation when either field is unknown.
            # If independently known spend already exceeds it, preserve that larger charge.
            charge = sum(known.values()) if complete else sum(max(known[key] or 0, row[bound]) for key, bound in
                (("input_tokens", "input_bound"), ("output_tokens", "output_bound")))
            db.execute("""UPDATE attempts SET status=?,input_tokens=?,output_tokens=?,charge=?,response_sha256=?,response_payload_sha256=?
                WHERE id=?""", ("known" if complete else "unresolved",
                str(known["input_tokens"]) if known["input_tokens"] is not None else None,
                str(known["output_tokens"]) if known["output_tokens"] is not None else None,
                str(charge), response_sha256, response_payload_sha256, attempt_id))
            if not complete or overrun:
                db.execute("UPDATE campaign SET halted=1,halt_reason=? WHERE id=1",
                           ("usage_contract_breach" if overrun else "usage_incomplete",))
        if overrun:
            raise DecisionError("decision_usage_exceeded_reservation")

    def finish(self, attempt_id, *, status, failure_kind=None, dispatched=False):
        with self._transaction() as db:
            row = self._attempt(db, attempt_id)
            if row["finished"] is not None:
                raise DecisionError("campaign_attempt_already_settled")
            final, charge = row["status"], row["charge"]
            if final == "reserved" and not dispatched:
                final, charge = "no_dispatch", 0
            elif final in {"reserved", "dispatching"}:
                final = "unresolved"
            elif final == "known":
                final = "settled"
            if final == "unresolved":
                db.execute("UPDATE campaign SET halted=1,halt_reason=coalesce(halt_reason,'transport_unknown') WHERE id=1")
            db.execute("UPDATE attempts SET status=?,charge=?,outcome=?,failure_kind=?,finished=? WHERE id=?",
                       (final, str(charge), status, failure_kind, time.time(), attempt_id))

    def halt(self, reason="accounting_failure"):
        with self._transaction() as db:
            db.execute("UPDATE campaign SET halted=1,halt_reason=? WHERE id=1", (reason,))

    def report(self):
        # Read-only connection: offline report/replay never changes ledger bytes.
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            campaign = dict(db.execute("SELECT * FROM campaign WHERE id=1").fetchone())
            attempts = [dict(x) for x in db.execute("SELECT * FROM attempts ORDER BY created,id")]
            versions = [dict(x) for x in db.execute("SELECT * FROM versions ORDER BY id")]
        for attempt in attempts:
            for key in ("charge", "input_tokens", "output_tokens"):
                if attempt[key] is not None:
                    attempt[key] = int(attempt[key])
        charged = sum(x["charge"] for x in attempts)
        return {"campaign_id": CAMPAIGN_ID, "limit": HARD_LIMIT, "halted": bool(campaign["halted"]),
                "halt_reason": campaign["halt_reason"], "charged_or_held": charged,
                "remaining": max(0, HARD_LIMIT - charged), "known_input_tokens": sum(x["input_tokens"] or 0 for x in attempts),
                "known_output_tokens": sum(x["output_tokens"] or 0 for x in attempts),
                "held": sum(x["charge"] for x in attempts if x["status"] in {"reserved", "dispatching", "unresolved"}),
                "attempts": attempts, "versions": versions}


class CampaignGovernor:
    """One frozen case/version/stage; passed to DecisionProvider(campaign=...)."""

    def __init__(self, ledger, *, version, stage, case_id, contract, allow_synthetic=False):
        self.ledger, self.version, self.stage = ledger, version, stage
        self.case_id, self.contract, self.allow_synthetic = case_id, contract.checked(), allow_synthetic

    @staticmethod
    def binding(endpoint, wire):
        return {"wire_sha256": digest(wire), "wire_bytes": len(wire), "url": endpoint.url,
                "provider": endpoint.public_identity()}

    def reserve(self, record, endpoint, wire):
        payload = decode(wire)
        if (endpoint.protocol != "systemone" or not self.allow_synthetic and (endpoint.url != URL or endpoint.model != MODEL)
                or set(payload) != {"model", "state", "questions"} or len(payload["questions"]) != 1
                or any(q.get("type") != "choice" for q in payload["questions"].values())
                or any(k.lower().startswith("typesafe-danger-zone") for k in endpoint.headers)):
            raise DecisionError("campaign_provider_not_admitted")
        self.ledger.reserve(attempt_id=record["attempt_id"], case_key=digest({"case": self.case_id, "version": self.version}),
            version=self.version, stage=self.stage, binding=self.binding(endpoint, wire), contract=self.contract)

    def dispatch(self, attempt_id, endpoint, wire):
        self.ledger.dispatch(attempt_id, self.binding(endpoint, wire))

    def response(self, attempt_id, usage, response_sha256, response_payload_sha256=None):
        self.ledger.response(attempt_id, usage, response_sha256, response_payload_sha256)

    def finish(self, attempt_id, **kwargs):
        self.ledger.finish(attempt_id, **kwargs)
