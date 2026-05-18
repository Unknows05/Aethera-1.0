"""
Shared utilities for the coin screener.
"""
from datetime import datetime


def calculate_win_rate(wins: int, losses: int) -> float:
    """Calculate win rate percentage from wins and losses."""
    closed = wins + losses
    return round((wins / closed * 100), 1) if closed > 0 else 0.0


def calculate_pnl_pct(entry: float, exit_price: float, signal_type: str) -> float:
    """
    Calculate PNL percentage for a trade.

    Args:
        entry: Entry price
        exit_price: Exit price
        signal_type: "LONG" or "SHORT"

    Returns:
        PNL percentage (positive for profit, negative for loss)
    """
    pnl = ((exit_price - entry) / entry) * 100
    return -pnl if signal_type == "SHORT" else pnl


def get_price_precision(price: float) -> int:
    """
    Get appropriate decimal precision for a price value.

    Returns:
        Number of decimal places based on price magnitude.
    """
    if price >= 1000:
        return 2
    elif price >= 1:
        return 4
    elif price >= 0.01:
        return 5
    else:
        return 7


def timestamp_to_date(timestamp: str) -> str:
    """Convert ISO timestamp to date string (YYYY-MM-DD)."""
    return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d")


def is_leveraged_token(symbol: str) -> bool:
    """Check if symbol is a leveraged token (UP/DOWN/BULL/BEAR)."""
    return any(s in symbol for s in ("UP", "DOWN", "BULL", "BEAR"))
