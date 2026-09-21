"""Safety policy: tiers, clamps, relative limits, confirmation tokens, rate limiting, show mode,
notch validation and ramp planning (DESIGN.md §10, BRIEF.md §4).

Every write path in the runtime goes through a :class:`Policy`; nothing here talks to the desk.
All numbers at this boundary are engineering units (dB, ms). Limits come from ``device.yaml``
(``policy`` for mix moves, ``detector.notch_max_db`` for notches) via the :class:`Descriptor`.

Decisions where DESIGN.md is silent:

* **Level floor.** Anything below −90 dB is the fader's bottom stop (float 0.0, printed ``-oo``,
  docs/research/scales_params.md §2.4), so :meth:`Policy.clamp_level` maps a finite request below
  −90 dB to ``-inf`` and *reports* it as a :class:`Clamped`; ``-inf`` itself passes untouched.
  ``mtx`` masters use the bus ceiling (device.yaml gives them the same clamp); ``dca`` the channel
  ceiling (a DCA fader is a channel-side offset, +10 dB top like a channel).
* **Rate limiter.** A token bucket with capacity = refill rate = ``writes_per_second``. A caller
  that finds the bucket empty *reserves* a token and sleeps until it would have refilled, so a
  burst is spread at exactly the configured rate. When the queued wait would exceed
  ``max_wait_s`` (1.0 s) the call raises ``RATE_LIMITED`` without reserving anything — so a
  single burst can be up to 2 × ``writes_per_second`` deep before rejections start (DESIGN's
  "51 writes in 1 s → raises" is the intent, not the exact number). ``panic=True`` never touches
  the bucket. Both the clock and the sleep are injectable for deterministic tests.
* **Tokens.** ``secrets.token_urlsafe(8)``; valid while ``clock() - minted < confirm_token_ttl_s``;
  deleted on every outcome (success, bad, expired). :meth:`Policy.guard` stores the action *and*
  the payload with the token and rejects the token (``BAD_TOKEN``) when the second call names a
  different action or a different payload, so a token can only ever execute the request whose
  summary the user saw. Payloads are compared canonically (type-exact: ``True`` is not ``1``), a
  token minted without an action cannot satisfy a guard that names one, minting a new token for an
  action revokes older unconfirmed tokens for the same action (one live summary per action), and at
  most 64 tokens are outstanding. Expired tokens are swept on every mint/consume. Callers bind into
  the payload whatever the summary showed that the arguments alone do not pin down (file digests,
  the resolved stage list, the open-mic set).
* **Show mode** blocks ``scene_recall``, ``scene_save``, ``setup_ringout_eqs``, ``ring_out`` and
  ``ring_out_system`` (tool-name spellings ``recall_scene``/``save_scene`` are accepted as aliases);
  ``restore_snapshot`` is allowed — it is the undo. ``force`` bypasses the relative limit even in
  show mode (show mode guards against accidents, ``force`` expresses intent).
* **Ramps.** ``n = ramp_ms / ramp_step_ms`` rounded half-up (300/20 → 15 values); ``-inf`` at
  either end is interpolated as −90 dB (the taper's last knee) and the final value is always
  exactly ``to_db``. A ramp of ≤ 0 ms, an unknown start (``None``/NaN) or a move under 0.05 dB
  is a single step ``[to_db]``.
* **Events** (all low-rate): ``policy.show_mode {on}``, ``policy.confirm_required {action, summary}``,
  ``policy.confirmed {action}``, ``policy.rate_limited {wait_s}``. Token values are never published.

Errors are :class:`PolicyError` with a machine ``code`` (``RELATIVE_TOO_LARGE``, ``SHOW_MODE_BLOCKS``,
``BAD_TOKEN``, ``TOKEN_EXPIRED``, ``RATE_LIMITED``, ``NOT_ALLOWED``, ``BOOST_FORBIDDEN``); clamps are
soft and reported through :class:`Clamped` (``reason`` ``CLAMPED_TO_LIMIT``), never raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Awaitable, Callable, Literal

from .descriptor import Descriptor
from .events import EventBus
from .scales import NEG_INF_DB
from .targets import Target

__all__ = [
    "Tier",
    "PolicyError",
    "Clamped",
    "PendingConfirmation",
    "Policy",
    "FADER_FLOOR_DB",
    "SHOW_MODE_BLOCKED",
]

log = logging.getLogger(__name__)

FADER_FLOOR_DB = -90.0  # below this the fader is at its bottom stop = -oo (scales_params.md §2.4)
_MIN_RAMP_DELTA_DB = 0.05  # moves smaller than this are one step: the desk grid is coarser anyway
_MAX_OUTSTANDING_TOKENS = 64
_EPS = 1e-9

SHOW_MODE_BLOCKED: frozenset[str] = frozenset({
    "scene_recall", "scene_save", "setup_ringout_eqs", "ring_out", "ring_out_system",
})
_ACTION_ALIASES: dict[str, str] = {  # tool names → policy action tokens
    "recall_scene": "scene_recall",
    "save_scene": "scene_save",
    "goscene": "scene_recall",
}


class Tier(IntEnum):
    """Write sensitivity of an address: READ always allowed, MIX clamped/shaped, GUARDED needs a token."""

    READ = 0
    MIX = 1
    GUARDED = 2


class PolicyError(Exception):
    """A refused request. ``code`` is the machine token the tool envelope carries."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            d.update(self.details)
        return d

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class Clamped:
    """A soft clamp that was applied: ``requested`` became ``value`` because of ``limit`` (all dB)."""

    value: float
    requested: float
    limit: float
    reason: str = "CLAMPED_TO_LIMIT"

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "requested": self.requested, "limit": self.limit, "reason": self.reason}


@dataclass
class PendingConfirmation:
    """First-call response of a Tier 2 action: show ``action_summary``, call again with ``confirm_token``.

    Field order differs from DESIGN §10 (defaulted ``requires_confirmation`` must come last in a
    dataclass); construct by keyword.
    """

    action_summary: str
    confirm_token: str
    expires_in_s: float
    requires_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "requires_confirmation": self.requires_confirmation,
            "action_summary": self.action_summary,
            "confirm_token": self.confirm_token,
            "expires_in_s": self.expires_in_s,
        }


@dataclass
class _Token:
    action: str | None
    summary: str
    payload: dict[str, Any]
    minted: float
    expires_at: float


def _canonical(payload: Any) -> str:
    """Type-exact, order-independent rendering of a token payload for comparison."""
    return json.dumps(payload, sort_keys=True, default=str, allow_nan=True)


def _num(value: Any, what: str) -> float:
    """Coerce to float; NaN (or a non-number) is a programming error, reported as NOT_ALLOWED."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError("NOT_ALLOWED", f"{what} must be a number, got {value!r}")
    f = float(value)
    if math.isnan(f):
        raise PolicyError("NOT_ALLOWED", f"{what} must be a number, got NaN")
    return f


class Policy:
    """Policy enforcer shared by the whole server (one instance per session).

    ``clock`` must be monotonic seconds (token TTL and the rate limiter use it); ``sleep`` is the
    awaitable the rate limiter waits with. Both default to the real thing.
    """

    def __init__(
        self,
        d: Descriptor,
        events: EventBus,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_wait_s: float = 1.0,
    ) -> None:
        self._d = d
        self._events = events
        self._clock = clock
        self._sleep = sleep
        p = d.policy
        self.ch_fader_max_db = float(p["ch_fader_max_db"])
        self.bus_fader_max_db = float(p["bus_fader_max_db"])
        self.main_fader_max_db = float(p["main_fader_max_db"])
        self.send_max_db = float(p["send_max_db"])
        self.eq_gain_abs_max_db = abs(float(p["eq_gain_abs_max_db"]))
        self.relative_max_db = abs(float(p["relative_max_db"]))
        self.relative_max_db_show_mode = abs(float(p["relative_max_db_show_mode"]))
        self.ramp_default_ms = max(0, int(p["ramp_default_ms"]))
        self.ramp_step_ms = max(1, int(p["ramp_step_ms"]))
        self.writes_per_second = float(p["writes_per_second"])
        if self.writes_per_second <= 0:
            raise ValueError("policy.writes_per_second must be > 0")
        self.confirm_token_ttl_s = float(p["confirm_token_ttl_s"])
        self.notch_max_db = -abs(float(d.detector.get("notch_max_db", -9)))
        self.max_wait_s = float(max_wait_s)
        self._show_mode = bool(p.get("show_mode_default", False))
        self.snapshot_before_write: bool = True  # cleared by mark_snapshot_taken()
        self._tokens: dict[str, _Token] = {}
        # token bucket (see module doc): capacity = refill/s = writes_per_second
        self._bucket_capacity = self.writes_per_second
        self._bucket_tokens = self.writes_per_second
        self._bucket_last = self._clock()

    # -- show mode ---------------------------------------------------------------------------
    @property
    def show_mode(self) -> bool:
        return self._show_mode

    @show_mode.setter
    def show_mode(self, on: bool) -> None:
        on = bool(on)
        changed = on != self._show_mode
        self._show_mode = on
        if changed:
            log.info("show mode %s", "ON" if on else "off")
            self._events.publish("policy.show_mode", on=on)

    def check_show_mode_allows(self, action: str) -> None:
        """Raise ``SHOW_MODE_BLOCKS`` when show mode is on and ``action`` is a blocked action
        (``scene_recall``, ``scene_save``, ``setup_ringout_eqs``, ``ring_out``, ``ring_out_system``)."""
        if not self._show_mode:
            return
        canon = _ACTION_ALIASES.get(action, action)
        if canon in SHOW_MODE_BLOCKED:
            raise PolicyError(
                "SHOW_MODE_BLOCKS",
                f"show mode is on: {action} is refused until show_mode(false)",
                action=action,
            )

    # -- tiers -------------------------------------------------------------------------------
    def tier_for(self, address: str) -> Tier:
        """Tier of a concrete OSC address (guarded globs win; unknown addresses are MIX)."""
        return Tier(self._d.tier_for(address))

    # -- clamps ------------------------------------------------------------------------------
    def level_ceiling_db(self, target: Target, *, kind: Literal["fader", "send"] = "fader") -> float:
        """The ceiling :meth:`clamp_level` applies (dB)."""
        if kind == "send":
            return self.send_max_db
        fam = target.family
        if fam == "main":
            return self.main_fader_max_db
        if fam in ("bus", "mtx"):
            return self.bus_fader_max_db
        return self.ch_fader_max_db  # ch, auxin, fxrtn, dca

    def clamp_level(
        self, target: Target, db: float, *, kind: Literal["fader", "send"] = "fader"
    ) -> tuple[float, Clamped | None]:
        """Apply the family ceiling (or the send ceiling) and the −90 dB floor. Never raises for a
        number; returns ``(value_db, Clamped | None)``. ``-inf`` passes through."""
        req = _num(db, "level")
        if req == NEG_INF_DB:
            return NEG_INF_DB, None
        limit = self.level_ceiling_db(target, kind=kind)
        if req > limit:
            return limit, Clamped(
                value=limit, requested=req, limit=limit,
                reason=f"CLAMPED_TO_LIMIT: {target.label} {kind} ceiling is {limit:+.1f} dB",
            )
        if req < FADER_FLOOR_DB:
            return NEG_INF_DB, Clamped(
                value=NEG_INF_DB, requested=req, limit=FADER_FLOOR_DB,
                reason=f"CLAMPED_TO_LIMIT: below the {FADER_FLOOR_DB:.0f} dB floor, fader fully down (-oo)",
            )
        return req, None

    def clamp_eq_gain(self, gain_db: float) -> tuple[float, Clamped | None]:
        """Limit an EQ band gain to ±``eq_gain_abs_max_db``."""
        req = _num(gain_db, "gain")
        lim = self.eq_gain_abs_max_db
        if req > lim:
            return lim, Clamped(value=lim, requested=req, limit=lim, reason=f"CLAMPED_TO_LIMIT: EQ gain ceiling {lim:+.1f} dB")
        if req < -lim:
            return -lim, Clamped(value=-lim, requested=req, limit=-lim, reason=f"CLAMPED_TO_LIMIT: EQ gain floor {-lim:+.1f} dB")
        return req, None

    @property
    def relative_limit_db(self) -> float:
        """Largest single-call relative move (dB) right now (tighter in show mode)."""
        return self.relative_max_db_show_mode if self._show_mode else self.relative_max_db

    def check_relative(self, delta_db: float, *, force: bool = False) -> None:
        """Raise ``RELATIVE_TOO_LARGE`` when ``|delta_db|`` exceeds the relative limit. ``force``
        bypasses the limit (also in show mode — see module doc)."""
        delta = _num(delta_db, "delta")
        if force:
            return
        limit = self.relative_limit_db
        if abs(delta) > limit + _EPS:
            raise PolicyError(
                "RELATIVE_TOO_LARGE",
                f"relative move of {delta:+.1f} dB exceeds the {limit:g} dB single-call limit"
                f"{' (show mode)' if self._show_mode else ''}; pass force=true only if the user explicitly asked",
                limit_db=limit, requested_db=delta,
            )

    # -- confirmation tokens -----------------------------------------------------------------
    def _sweep_tokens(self, now: float) -> None:
        dead = [t for t, tok in self._tokens.items() if now - tok.minted >= self.confirm_token_ttl_s]
        for t in dead:
            del self._tokens[t]

    def require_confirmation(
        self, action_summary: str, payload: dict[str, Any], *, action: str | None = None
    ) -> PendingConfirmation:
        """Mint a single-use token (TTL ``confirm_token_ttl_s``) bound to ``payload`` and, when given,
        to ``action``. Returns the envelope the tool should hand back."""
        now = self._clock()
        self._sweep_tokens(now)
        if action is not None:
            # One live summary per action: minting a new one revokes whatever the user was shown before
            # and did not confirm, so a declined (or superseded) request cannot be replayed later.
            stale = [t for t, tok in self._tokens.items() if tok.action == action]
            for t in stale:
                del self._tokens[t]
            if stale:
                log.info("confirmation for %s re-issued: %d earlier unconfirmed token(s) revoked", action, len(stale))
        while len(self._tokens) >= _MAX_OUTSTANDING_TOKENS:  # bounded memory whatever a client does
            del self._tokens[next(iter(self._tokens))]
        token = secrets.token_urlsafe(8)
        while token in self._tokens:  # astronomically unlikely, but single-use must stay single-use
            token = secrets.token_urlsafe(8)
        self._tokens[token] = _Token(
            action=action, summary=action_summary, payload=dict(payload), minted=now,
            expires_at=now + self.confirm_token_ttl_s,
        )
        log.info("confirmation required: %s", action_summary)
        self._events.publish("policy.confirm_required", action=action, summary=action_summary)
        return PendingConfirmation(action_summary=action_summary, confirm_token=token, expires_in_s=self.confirm_token_ttl_s)

    def consume_token(self, token: str, *, expected_action: str | None = None) -> dict[str, Any]:
        """Redeem ``token`` and return its payload. Raises ``BAD_TOKEN`` (unknown, already used, or
        minted for another action) or ``TOKEN_EXPIRED``. The token is deleted on every outcome."""
        now = self._clock()
        tok = self._tokens.pop(str(token), None) if token is not None else None
        self._sweep_tokens(now)  # after the pop, so an expired token reports TOKEN_EXPIRED, not BAD_TOKEN
        if tok is None:
            raise PolicyError("BAD_TOKEN", "unknown or already used confirm_token; call again without a token to get a new one")
        if now - tok.minted >= self.confirm_token_ttl_s:
            raise PolicyError("TOKEN_EXPIRED", f"confirm_token expired after {self.confirm_token_ttl_s:g} s; call again without a token")
        if expected_action is not None and tok.action != expected_action:
            raise PolicyError(
                "BAD_TOKEN",
                f"confirm_token was issued for {tok.action or 'an unnamed action'!r}, not {expected_action!r}; call again without a token",
            )
        self._events.publish("policy.confirmed", action=tok.action or expected_action, summary=tok.summary)
        return tok.payload

    def guard(
        self, action: str, summary: str, payload: dict[str, Any], confirm_token: str | None
    ) -> dict[str, Any] | PendingConfirmation:
        """The Tier 2 dance in one call: no token → :class:`PendingConfirmation`; a token → the
        validated payload. The token must have been minted for the same ``action`` *and* the same
        ``payload`` (``BAD_TOKEN`` otherwise) so it cannot execute anything but what was summarised."""
        if confirm_token is None or confirm_token == "":
            return self.require_confirmation(summary, payload, action=action)
        stored = self.consume_token(confirm_token, expected_action=action)
        if _canonical(stored) != _canonical(dict(payload)):  # not ==: True == 1 == 1.0 in Python, not in a summary
            raise PolicyError(
                "BAD_TOKEN",
                f"confirm_token was issued for a different {action} request; call again without a token",
            )
        return stored

    @property
    def pending_confirmations(self) -> int:
        """Outstanding (unexpired) tokens — for status/diagnostics."""
        self._sweep_tokens(self._clock())
        return len(self._tokens)

    # -- rate limiter ------------------------------------------------------------------------
    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._bucket_last)
        self._bucket_last = now
        self._bucket_tokens = min(self._bucket_capacity, self._bucket_tokens + elapsed * self.writes_per_second)

    async def acquire_write(self, *, panic: bool = False) -> None:
        """Take one write slot from the bucket, sleeping (≤ ``max_wait_s``) when it is empty.
        Raises ``RATE_LIMITED`` instead of waiting longer. ``panic=True`` bypasses everything."""
        if panic:
            return
        now = self._clock()
        self._refill(now)
        if self._bucket_tokens >= 1.0:
            self._bucket_tokens -= 1.0
            return
        wait = (1.0 - self._bucket_tokens) / self.writes_per_second
        if wait > self.max_wait_s + _EPS:
            log.warning("rate limited: next write slot in %.2f s (> %.1f s)", wait, self.max_wait_s)
            self._events.publish("policy.rate_limited", wait_s=wait)
            raise PolicyError(
                "RATE_LIMITED",
                f"too many writes: the next slot is {wait:.2f} s away (limit {self.writes_per_second:g}/s); retry shortly",
                wait_s=wait,
            )
        self._bucket_tokens -= 1.0  # reserve the slot, then wait for it to refill
        log.debug("rate limiter: waiting %.3f s", wait)
        await self._sleep(wait)

    @property
    def write_slots_available(self) -> float:
        """Tokens in the bucket right now (fractional; negative = queued reservations)."""
        self._refill(self._clock())
        return self._bucket_tokens

    # -- notches -----------------------------------------------------------------------------
    def validate_notch(self, current_db: float, new_db: float) -> None:
        """CFS² may only cut: ``new_db`` ≤ 0, ≤ ``current_db`` and not deeper than
        ``detector.notch_max_db`` (−9). Raises ``BOOST_FORBIDDEN`` / ``NOT_ALLOWED``."""
        cur = _num(current_db, "current gain")
        new = _num(new_db, "new gain")
        if new > _EPS:
            raise PolicyError("BOOST_FORBIDDEN", f"notch gain {new:+.1f} dB is a boost; CFS² only cuts")
        if new > cur + _EPS:
            raise PolicyError(
                "BOOST_FORBIDDEN",
                f"raising a band from {cur:+.1f} to {new:+.1f} dB is a boost; CFS² only deepens cuts",
            )
        if new < self.notch_max_db - _EPS:
            raise PolicyError(
                "NOT_ALLOWED",
                f"notch {new:+.1f} dB is deeper than the {self.notch_max_db:+.0f} dB limit",
                limit_db=self.notch_max_db,
            )

    # -- ramps -------------------------------------------------------------------------------
    @property
    def ramp_step_s(self) -> float:
        """Interval between ramp steps in seconds (``ramp_step_ms`` / 1000)."""
        return self.ramp_step_ms / 1000.0

    def ramp_steps(self, from_db: float | None, to_db: float, ramp_ms: int | None = None) -> list[float]:
        """dB values to write, one every ``ramp_step_ms``, ending exactly at ``to_db``.

        ``ramp_ms`` ``None`` → ``ramp_default_ms``; ≤ 0 → ``[to_db]``. ``-inf`` endpoints are
        interpolated from/to −90 dB (the taper's bottom, scales_params.md §2.4); when ``to_db`` is
        ``-inf`` the final value is ``-inf``. A move under 0.05 dB is a single step.
        """
        end = _num(to_db, "to_db")
        ms = self.ramp_default_ms if ramp_ms is None else int(ramp_ms)
        if from_db is None or ms <= 0:
            return [end]
        try:
            start = _num(from_db, "from_db")
        except PolicyError:
            return [end]
        a = FADER_FLOOR_DB if start == NEG_INF_DB else start
        b = FADER_FLOOR_DB if end == NEG_INF_DB else end
        if not math.isfinite(a) or not math.isfinite(b) or abs(b - a) < _MIN_RAMP_DELTA_DB:
            return [end]
        n = max(1, math.floor(ms / self.ramp_step_ms + 0.5))
        steps = [a + (b - a) * i / n for i in range(1, n)]
        steps.append(end)
        return steps

    # -- snapshot-before-write ---------------------------------------------------------------
    def mark_snapshot_taken(self) -> None:
        """Record that the session's pre-write snapshot exists (clears :attr:`snapshot_before_write`)."""
        self.snapshot_before_write = False

    def __repr__(self) -> str:
        return (
            f"Policy(show_mode={self._show_mode}, ceilings ch={self.ch_fader_max_db:+g} bus={self.bus_fader_max_db:+g} "
            f"main={self.main_fader_max_db:+g} send={self.send_max_db:+g}, {self.writes_per_second:g} writes/s)"
        )
