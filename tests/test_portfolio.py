import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import portfolio


class PortfolioTest(unittest.TestCase):
    def test_load_excludes_closed_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "portfolio.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "ticker",
                        "company_name",
                        "sector",
                        "holding_status",
                        "quantity",
                        "avg_cost",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "ticker": "005930",
                            "company_name": "삼성전자",
                            "sector": "반도체",
                            "holding_status": "active",
                            "quantity": "1",
                            "avg_cost": "70000",
                        },
                        {
                            "ticker": "267270",
                            "company_name": "HD현대건설기계",
                            "sector": "기계",
                            "holding_status": "closed",
                            "quantity": "0",
                            "avg_cost": "0",
                        },
                    ]
                )

            with patch.object(portfolio, "SSOT_CSV", csv_path):
                stocks = portfolio.load()

        self.assertEqual([stock["code"] for stock in stocks], ["005930"])


if __name__ == "__main__":
    unittest.main()
