#!/usr/bin/env python3
"""Create and validate deterministic incident records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = {"observed", "reproduced", "diagnosed", "verified", "unresolved"}
CLASSIFICATIONS = {
    "domain_behavior",
    "prediction_model",
    "observation_decoder",
    "actuator_input",
    "timing_protocol",
    "environment",
    "missing_capability",
    "unknown",
}
SEVERITIES = {"low", "medium", "high", "catastrophic"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result[:48] or "incident"


def make_record(args: argparse.Namespace) -> dict[str, Any]:
    artifact = Path(args.live_artifact).expanduser().resolve() if args.live_artifact else None
    if artifact and not artifact.is_file():
        raise SystemExit(f"live artifact does not exist: {artifact}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "schema_version": 1,
        "id": f"{stamp}-{slug(args.summary)}",
        "status": "observed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": args.summary,
        "component": args.component,
        "symptom": args.symptom,
        "classification": args.classification,
        "severity": args.severity,
        "live_state": {
            "required": bool(artifact),
            "preserved": bool(artifact),
            "artifact": str(artifact) if artifact else None,
            "sha256": sha256(artifact) if artifact else None,
            "canonical_state": {},
            "restored_after_probe": None,
        },
        "unexpected": {
            "predicted": args.predicted,
            "actual": args.actual,
            "diff": [],
            "extra_state_deltas": [],
        },
        "authority": {"oracle": "", "sources": []},
        "reproduction": {
            "deterministic": False,
            "fixture": None,
            "fixture_sha256": None,
            "command": "",
            "steps": [],
            "run_count": 0,
            "same_outcome": False,
            "observed": "",
        },
        "hypotheses": [],
        "root_cause": {"verified": False, "claim": "", "evidence": [], "alternatives_falsified": []},
        "solution": {"kind": None, "files": [], "contract_change": "", "workaround": None},
        "verification": {
            "targeted_checks": [],
            "regression_checks": [],
            "replay": {"passed": False, "runs": 0, "expected": "", "actual": "", "extra_state_deltas": []},
            "metrics": {},
        },
        "learning": {"domain_knowledge": [], "regression_cases": [], "tooling_contracts": []},
        "action_changing_unknowns": [],
        "next_experiments": [],
    }


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_list(entries: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(entries, list):
        errors.append(f"{label} must be a list")
        return []
    return entries


def validate(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key in (
        "schema_version", "id", "status", "summary", "component", "symptom",
        "classification", "severity", "live_state", "unexpected", "authority",
        "reproduction", "hypotheses", "root_cause", "solution", "verification",
        "learning", "action_changing_unknowns", "next_experiments",
    ):
        if key not in record:
            errors.append(f"missing {key}")
    if errors:
        return errors, warnings
    if record["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if record["status"] not in STATUSES:
        errors.append(f"status must be one of {sorted(STATUSES)}")
    if record["classification"] not in CLASSIFICATIONS:
        errors.append(f"classification must be one of {sorted(CLASSIFICATIONS)}")
    if record["severity"] not in SEVERITIES:
        errors.append(f"severity must be one of {sorted(SEVERITIES)}")
    for key in ("id", "summary", "component", "symptom"):
        if not nonempty(record[key]):
            errors.append(f"{key} must be non-empty")

    unexpected = record["unexpected"]
    for key in ("predicted", "actual"):
        if not nonempty(unexpected.get(key)):
            errors.append(f"unexpected.{key} must be non-empty; use an explicit unknown statement")
    check_list(unexpected.get("diff"), "unexpected.diff", errors)
    check_list(unexpected.get("extra_state_deltas"), "unexpected.extra_state_deltas", errors)

    live = record["live_state"]
    if live.get("required"):
        if not live.get("preserved"):
            errors.append("live_state.preserved must be true when preservation is required")
        artifact = live.get("artifact")
        if not nonempty(artifact) or not Path(artifact).is_file():
            errors.append("live_state.artifact must reference an existing file")
        elif live.get("sha256") != sha256(Path(artifact)):
            errors.append("live_state.sha256 does not match artifact")

    status = record["status"]
    reproduction = record["reproduction"]
    at_least_reproduced = status in {"reproduced", "diagnosed", "verified"}
    if at_least_reproduced:
        if not reproduction.get("deterministic") or not reproduction.get("same_outcome"):
            errors.append("reproduction must be deterministic with same_outcome true")
        if int(reproduction.get("run_count", 0)) < 2:
            errors.append("reproduction.run_count must be at least 2")
        for key in ("fixture", "command", "observed"):
            if not nonempty(reproduction.get(key)):
                errors.append(f"reproduction.{key} must be non-empty")
        if not check_list(reproduction.get("steps"), "reproduction.steps", errors):
            errors.append("reproduction.steps must not be empty")
        authority = record["authority"]
        if not nonempty(authority.get("oracle")):
            errors.append("authority.oracle must be non-empty")
        if not check_list(authority.get("sources"), "authority.sources", errors):
            errors.append("authority.sources must not be empty")

    if status in {"diagnosed", "verified"}:
        root = record["root_cause"]
        if not root.get("verified") or not nonempty(root.get("claim")):
            errors.append("root_cause must be verified with a non-empty claim")
        if not check_list(root.get("evidence"), "root_cause.evidence", errors):
            errors.append("root_cause.evidence must not be empty")
        if not check_list(root.get("alternatives_falsified"), "root_cause.alternatives_falsified", errors):
            warnings.append("no alternative hypotheses are recorded as falsified")

    if status == "verified":
        solution = record["solution"]
        if solution.get("kind") not in {"code_fix", "knowledge_fix", "interface_fix", "environment_fix"}:
            errors.append("solution.kind must identify the verified fix class")
        verification = record["verification"]
        for group in ("targeted_checks", "regression_checks"):
            checks = check_list(verification.get(group), f"verification.{group}", errors)
            if not checks or any(not isinstance(item, dict) or item.get("passed") is not True for item in checks):
                errors.append(f"verification.{group} must contain only passing checks")
        replay = verification.get("replay", {})
        if replay.get("passed") is not True or int(replay.get("runs", 0)) < 2:
            errors.append("verification.replay must pass at least 2 runs")
        if replay.get("extra_state_deltas"):
            errors.append("verification.replay.extra_state_deltas must be empty")
        if live.get("required") and live.get("restored_after_probe") is not True:
            errors.append("live state must be authoritatively restored after probes")
        learning = record["learning"]
        retained = sum(len(check_list(learning.get(key), f"learning.{key}", errors)) for key in (
            "domain_knowledge", "regression_cases", "tooling_contracts"
        ))
        if retained < 1:
            errors.append("verified incident must retain knowledge, a regression case, or a tooling contract")

    if status == "unresolved":
        experiments = check_list(record["next_experiments"], "next_experiments", errors)
        if not experiments:
            errors.append("unresolved incident requires next_experiments")
        for index, item in enumerate(experiments):
            for key in ("hypothesis", "action", "expected_discriminator", "risk", "cost", "rollback"):
                if not isinstance(item, dict) or not nonempty(item.get(key)):
                    errors.append(f"next_experiments[{index}].{key} must be non-empty")
        if record["root_cause"].get("verified"):
            errors.append("unresolved incident cannot assert a verified root cause")

    if record["classification"] == "unknown" and status == "verified":
        errors.append("verified incident cannot retain unknown classification")
    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a new observed incident")
    init.add_argument("path")
    init.add_argument("--summary", required=True)
    init.add_argument("--component", required=True)
    init.add_argument("--symptom", required=True)
    init.add_argument("--predicted", required=True)
    init.add_argument("--actual", required=True)
    init.add_argument("--classification", choices=sorted(CLASSIFICATIONS), default="unknown")
    init.add_argument("--severity", choices=sorted(SEVERITIES), default="medium")
    init.add_argument("--live-artifact")
    validate_parser = sub.add_parser("validate", help="validate an incident promotion record")
    validate_parser.add_argument("path")
    args = parser.parse_args()

    path = Path(args.path).expanduser().resolve()
    if args.command == "init":
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing incident: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = make_record(args)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        errors, warnings = validate(record)
        print(json.dumps({"path": str(path), "valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
        raise SystemExit(1 if errors else 0)

    record = json.loads(path.read_text(encoding="utf-8"))
    errors, warnings = validate(record)
    print(json.dumps({"path": str(path), "valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
