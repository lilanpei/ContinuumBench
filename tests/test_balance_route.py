"""TDD for the `balance` route primitive: an undifferentiated round-robin across a worker pool."""

from __future__ import annotations

from continuum_bench.scenarios import RouteSpec


def test_route_spec_has_balance_pool_field_defaulting_empty():
    r = RouteSpec(mode="single", target="X")
    assert r.balance_pool == ""           # additive, defaulted, does not break existing call sites


def test_route_spec_balance_mode_carries_pool_name():
    r = RouteSpec(mode="balance", balance_pool="Elastic")
    assert r.mode == "balance" and r.balance_pool == "Elastic"
