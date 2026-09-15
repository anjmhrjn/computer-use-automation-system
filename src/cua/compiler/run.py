"""A loaded discovery run: the transcript's bracketing entries pulled out by
type, plus access to the AX snapshots written beside it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cua.discovery import (
    Contract,
    ContractDeclared,
    RunFinished,
    RunStarted,
    TurnRecord,
    read,
)
from cua.surface import Observation

from .errors import NotCompilable


@dataclass(frozen=True)
class DiscoveryRun:
    path: Path
    started: RunStarted
    contract: Contract
    turns: list[TurnRecord]
    finished: RunFinished

    def snapshot(self, name: str) -> Observation:
        return Observation.model_validate_json((self.path.parent / name).read_text())


def load(path: Path) -> DiscoveryRun:
    entries = read(path)
    started = [e for e in entries if isinstance(e, RunStarted)]
    contracts = [e for e in entries if isinstance(e, ContractDeclared)]
    finished = [e for e in entries if isinstance(e, RunFinished)]
    if len(started) != 1 or len(contracts) != 1 or len(finished) != 1:
        raise NotCompilable(
            path,
            None,
            "one run_started, one contract_declared and one run_finished",
            f"{len(started)}, {len(contracts)} and {len(finished)}",
        )
    return DiscoveryRun(
        path=path,
        started=started[0],
        contract=contracts[0].contract,
        turns=[e for e in entries if isinstance(e, TurnRecord)],
        finished=finished[0],
    )
