import unittest
from unittest.mock import patch

from app.utils import token_cost


class TokenCostTests(unittest.TestCase):
    def test_calculates_exact_known_model_cost(self) -> None:
        cost = token_cost.calculate_cost("gpt-4o-mini", 1_000_000, 1_000_000)

        self.assertEqual(cost, 0.75)

    def test_rounds_to_six_decimal_places(self) -> None:
        cost = token_cost.calculate_cost("gpt-4o-mini", 1, 1)

        self.assertEqual(cost, 0.000001)

    def test_unknown_model_falls_back_and_logs_warning(self) -> None:
        with patch.object(token_cost.logger, "warning") as warning:
            cost = token_cost.calculate_cost("unknown-model", 1_000_000, 1_000_000)

        self.assertEqual(cost, 18.0)
        warning.assert_called_once()
