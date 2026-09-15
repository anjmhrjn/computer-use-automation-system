"""Polls observations until a predicate holds or a deadline passes. Shared by the
replay engine (step postconditions) and the discovery loop (the model's declared
expectation), so both verify a step the same way.

No fixed delay: each iteration is a fresh observation, and `observe()` itself blocks
on the surface's own readiness (invariant 2). A surface that is not ready yet is
"not yet"; the deadline decides when it is "never". Every observation is screened
first, because an interstitial is exactly the page that keeps a predicate from ever
holding."""

from __future__ import annotations

import time

from cua.schema import AppProfile, Predicate
from cua.surface import Observation, SurfaceNotReady

from .classify import screen
from .errors import InterstitialDetected, PostconditionTimeout
from .predicates import describe, holds
from .session import Session


def screened(observation: Observation, profile: AppProfile, session: Session, attempts: int = 0) -> Observation:
    blocking = screen(observation, profile, session)
    if blocking is not None:
        raise InterstitialDetected(session.step_id, blocking, attempts, observation)
    return observation


def await_predicate(
    predicate: Predicate,
    timeout_ms: int,
    profile: AppProfile,
    session: Session,
    attempts: int = 0,
) -> Observation:
    """Returns the observation that satisfied the predicate."""
    deadline = time.monotonic() + timeout_ms / 1000
    summary = "surface never became ready"
    while True:
        try:
            observation = screened(session.observe(), profile, session, attempts)
        except SurfaceNotReady:
            observation = None
        if observation is not None:
            if holds(predicate, observation, session):
                return observation
            summary = summarize(observation)
        if time.monotonic() >= deadline:
            raise PostconditionTimeout(session.step_id, describe(predicate), summary)


def summarize(observation: Observation) -> str:
    return f"location {observation.location!r}, {len(observation.nodes)} nodes"
