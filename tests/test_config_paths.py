from core.config import PORTFOLIO_FILE


def test_portfolio_file_points_to_existing_ssot() -> None:
    assert PORTFOLIO_FILE == (
        PORTFOLIO_FILE.parents[2] / "개인투자비서 Agent" / "data" / "portfolio.csv"
    )
    assert PORTFOLIO_FILE.is_file()
