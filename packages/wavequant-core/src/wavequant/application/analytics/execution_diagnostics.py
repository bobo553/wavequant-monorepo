"""Summarize execution evidence without treating an idle account as a strategy pass."""
from collections import Counter


def execution_diagnostics(result):
    buys = [o for o in result.orders if o['side']=='BUY']
    filled = [o for o in buys if o['status']=='filled']
    return dict(
        status=('no_entry_signals_reached_execution' if not buys else
                'all_entry_attempts_rejected' if not filled else
                'open_positions_without_closed_trades' if not result.trades else 'trades_observed'),
        entry_attempts=len(buys), entry_fills=len(filled), closed_trades=len(result.trades),
        open_positions=len(result.open_positions),
        rejection_reasons=dict(Counter(o['reason'] for o in buys if o['status']=='cancelled')),
        warning='No fills or too few trades cannot establish profitability; inspect constraints, do not force orders.')
