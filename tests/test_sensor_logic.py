"""Dependency-free regression tests for sensor value normalization."""

from __future__ import annotations

import ast
import math
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SENSOR_PATH = ROOT / "custom_components" / "orvibo_lan" / "sensor.py"
PROFILES_PATH = ROOT / "custom_components" / "orvibo_lan" / "device_profiles.py"


def _function_nodes(path: Path, names: set[str]) -> list[ast.stmt]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]


def _load_helpers() -> dict[str, object]:
    namespace: dict[str, object] = {
        "Any": Any,
        "Mapping": Mapping,
        "Optional": Optional,
        "PROPERTY_BATTERY_POWER": "battery_power",
        "State": Mapping,
        "math": math,
    }
    profile_nodes = _function_nodes(
        PROFILES_PATH,
        {"state_properties", "property_value", "property_percentage"},
    )
    exec(
        compile(
            ast.Module(body=profile_nodes, type_ignores=[]),
            str(PROFILES_PATH),
            "exec",
        ),
        namespace,
    )
    sensor_nodes = _function_nodes(
        SENSOR_PATH,
        {"_safe_float", "_safe_percentage", "_get_battery", "_get_measurement"},
    )
    exec(
        compile(
            ast.Module(body=sensor_nodes, type_ignores=[]),
            str(SENSOR_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace


class SensorValueTests(unittest.TestCase):
    """Verify actual helper functions without importing Home Assistant."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.helpers = _load_helpers()

    def test_measurement_precedence_and_zero_values(self) -> None:
        get_measurement = self.helpers["_get_measurement"]
        self.assertEqual(
            get_measurement({"temperature": 0}, "temperature", "value1"),
            0,
        )
        self.assertEqual(
            get_measurement(
                {"properties": {"temperature": {"value": "21.5"}}},
                "temperature",
                "value1",
            ),
            21.5,
        )
        self.assertEqual(
            get_measurement({"value2": "0"}, "humidity", "value2"),
            0,
        )

    def test_battery_range_and_fallbacks(self) -> None:
        get_battery = self.helpers["_get_battery"]
        self.assertEqual(get_battery({"battery": 0}), 0)
        self.assertEqual(get_battery({"battery": 101, "value4": 55}), 55)
        self.assertEqual(
            get_battery({"properties": {"battery_power": {"percent": 88}}}),
            88,
        )
        self.assertEqual(
            get_battery({"properties": {"battery": {"power": 42}}}),
            42,
        )

    def test_invalid_numeric_values_are_rejected(self) -> None:
        safe_float = self.helpers["_safe_float"]
        safe_percentage = self.helpers["_safe_percentage"]
        self.assertIsNone(safe_float(True))
        self.assertIsNone(safe_float("nan"))
        self.assertIsNone(safe_float("inf"))
        self.assertIsNone(safe_percentage(-1))
        self.assertIsNone(safe_percentage(101))


if __name__ == "__main__":
    unittest.main()
