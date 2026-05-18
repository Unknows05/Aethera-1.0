"""
Display — Console table (rich) + Telegram message formatter.
"""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel


console = Console()


def print_screen_result(signals: list[dict], elapsed_seconds: float):
    """Print screening results as a colored console table."""
    table = Table(
        title=f"[bold cyan]Coin Screener[/bold cyan] — {len(signals)} coins ({elapsed_seconds:.1f}s)",
        title_justify="left",
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
        collapse_padding=False,
    )

    table.add_column("#", width=3)
    table.add_column("COIN", width=12)
    table.add_column("PRICE", width=12, justify="right")
    table.add_column("REGIME", width=9)
    table.add_column("SIGNAL", width=7)
    table.add_column("CONF%", width=6, justify="right")
    table.add_column("ENTRY", width=12, justify="right")
    table.add_column("SL", width=12, justify="right")
    table.add_column("TP", width=12, justify="right")

    sorted_signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    def fmt_price(val):
        if val is None:
            return "—"
        if val >= 1000:
            return f"${val:,.0f}"
        elif val >= 1:
            return f"${val:,.2f}"
        elif val >= 0.01:
            return f"${val:,.4f}"
        else:
            return f"${val:,.6f}"

    for i, s in enumerate(sorted_signals, 1):
        if s["signal"] == "LONG":
            sig_style = "bold green"
        elif s["signal"] == "SHORT":
            sig_style = "bold red"
        else:
            sig_style = "dim"

        conf = s["confidence"]
        conf_color = "[bold green]" if conf >= 70 else "[yellow]" if conf >= 50 else "[dim]"

        regime_colors = {
            "BULL": "[green]", "BEAR": "[red]",
            "SIDEWAYS": "[yellow]", "HIGH_VOL": "[magenta]"
        }
        regime_str = f"{regime_colors.get(s['regime'], '')}{s['regime']}"

        table.add_row(
            str(i),
            s["symbol"],
            fmt_price(s["price"]),
            regime_str,
            f"[{sig_style}]{s['signal']}[/]",
            f"{conf_color}{conf}%[/]",
            fmt_price(s["entry"]),
            fmt_price(s["sl"]),
            fmt_price(s["tp"]),
        )

    console.print()
    console.print(table)

    # Summary
    longs = sum(1 for s in signals if s["signal"] == "LONG")
    shorts = sum(1 for s in signals if s["signal"] == "SHORT")
    waits = sum(1 for s in signals if s["signal"] == "WAIT")

    top_signals = [s for s in sorted_signals if s["signal"] in ("LONG", "SHORT")][:5]

    summary = f"[bold]Summary:[/bold] {longs} LONG | {shorts} SHORT | {waits} WAIT"
    if top_signals:
        top_str = " | ".join(
            f"[{'green' if s['signal'] == 'LONG' else 'red'}]{s['symbol']} {s['signal']} ({s['confidence']}%)[/]"
            for s in top_signals
        )
        summary += f"\n  Top: {top_str}"
    else:
        summary += "\n  No active signals — market conditions neutral."

    console.print(Panel(summary, title="📊 Screening Summary", border_style="cyan"))
    console.print()

    # Show pattern details for coins with patterns
    pattern_coins = [s for s in sorted_signals if s.get("patterns_detected")]
    if pattern_coins:
        pattern_lines = []
        for s in pattern_coins[:5]:
            patterns = ", ".join(s["patterns_detected"])
            pattern_lines.append(f"  {s['symbol']}: {patterns}")
        console.print(Panel("\n".join(pattern_lines), title="📐 Patterns Detected", border_style="yellow"))
        console.print()


def print_status(status_text: str):
    """Print a status message."""
    console.print(f"[dim]{status_text}[/dim]")


def print_error(error_text: str):
    """Print an error message."""
    console.print(f"[bold red]❌ {error_text}[/bold red]")


def format_telegram_message(signals: list[dict], elapsed: float, mode: str = "full") -> str:
    """
    Format screening results for Telegram.

    Args:
        mode: "full" = all coins, "signals" = only LONG/SHORT > threshold
    """
    if mode == "signals":
        filtered = [s for s in signals if s["signal"] in ("LONG", "SHORT")]
        if not filtered:
            return "📊 <b>No active signals</b>\n\nNo coins passed signal threshold right now."
        signals = filtered

    sorted_signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    # Header
    lines = [
        f"📊 <b>Coin Screener</b> — {len(sorted_signals)} results ({elapsed:.1f}s)",
        ""
    ]

    for i, s in enumerate(sorted_signals, 1):
        # Emoji based on signal
        if s["signal"] == "LONG":
            emoji = "🟢"
        elif s["signal"] == "SHORT":
            emoji = "🔴"
        else:
            emoji = "⚪"

        # Confidence bar
        bars = int(s["confidence"] / 5)
        bar = "█" * bars + "░" * (20 - bars)

        lines.append(f"{emoji} <b>#{i} {s['symbol']}</b> — {s['signal']}")
        lines.append(f"   Price: ${s['price']:,.{4 if s['price'] < 1 else 2}f} | Score: {s['score']:.0f}")
        lines.append(f"   [{bar}] {s['confidence']}%")

        if s["entry"] is not None:
            lines.append(f"   Entry: ${s['entry']:,.{4 if s['entry'] < 1 else 2}f}")
            if s["sl"]:
                sl_pct = abs((s["sl"] - s["entry"]) / s["entry"] * 100)
                lines.append(f"   SL: ${s['sl']:,.{4 if s['sl'] < 1 else 2}f} ({sl_pct:.1f}%)")
            if s["tp"]:
                tp_pct = abs((s["tp"] - s["entry"]) / s["entry"] * 100)
                lines.append(f"   TP: ${s['tp']:,.{4 if s['tp'] < 1 else 2}f} ({tp_pct:.1f}%)")

        if s["reasons"]:
            lines.append(f"   📝 {s['reasons'][0]}")

        lines.append("")  # Blank line between coins

    # Summary
    longs = sum(1 for s in signals if s["signal"] == "LONG")
    shorts = sum(1 for s in signals if s["signal"] == "SHORT")
    waits = sum(1 for s in signals if s["signal"] == "WAIT")

    lines.append(f"<b>Summary:</b> {longs} LONG | {shorts} SHORT | {waits} WAIT")

    return "\n".join(lines)


def format_status_message(running: bool, last_scan: str, total_coins: int,
                           signals_count: int, mode: str) -> str:
    """Format bot status for Telegram."""
    status_emoji = "🟢" if running else "🔴"
    status_text = "Running" if running else "Stopped"

    return (
        f"{status_emoji} <b>Coin Screener Bot</b>\n\n"
        f"Status: {status_text}\n"
        f"Last Scan: {last_scan}\n"
        f"Coins Scanned: {total_coins}\n"
        f"Active Signals: {signals_count}\n"
        f"Mode: {mode}"
    )
