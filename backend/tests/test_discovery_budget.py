"""Tests for proportional budget distribution across seed artists."""
from __future__ import annotations

from musicmind.api.recommendations.fetch import allocate_seed_budget


def test_budget_proportional_to_affinity() -> None:
    seeds = [("Top", 1.0), ("Mid", 0.5), ("Tail", 0.1)]
    allocation = allocate_seed_budget(seeds, total_budget=40)
    assert allocation["Top"] > allocation["Mid"] > allocation["Tail"]
    assert sum(allocation.values()) <= 40
    assert all(v >= 1 for v in allocation.values())


def test_uniform_seeds_get_equal_budget() -> None:
    seeds = [("A", 1.0), ("B", 1.0), ("C", 1.0)]
    allocation = allocate_seed_budget(seeds, total_budget=30)
    assert allocation["A"] == allocation["B"] == allocation["C"] == 10


def test_empty_seeds() -> None:
    assert allocate_seed_budget([], total_budget=20) == {}


def test_minimum_one_per_seed() -> None:
    seeds = [("Big", 1.0), ("Tiny", 0.001)]
    allocation = allocate_seed_budget(seeds, total_budget=10)
    assert allocation["Tiny"] >= 1
