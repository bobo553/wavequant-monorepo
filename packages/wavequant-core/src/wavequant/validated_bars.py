"""An immutable sequence whose OHLC/order checks can be reused by observers."""
from collections.abc import Sequence
from dataclasses import dataclass

from .model import Bar


@dataclass(frozen=True, init=False)
class ValidatedBars(Sequence):
    _values: tuple
    def __init__(self, bars):
        from .wave_strength import validate_prefix
        values = tuple(bars)
        # Only frozen model Bars qualify, not mutable duck-typed records.
        if any(type(b) is not Bar for b in values):
            raise ValueError('immutable Bar records required')
        if values:
            validate_prefix(values, symbol=values[0].symbol, end=len(values)-1)
        object.__setattr__(self, '_values', values)

    def __add__(self, other):
        # General concatenation does NOT inherit the validation certificate.
        return self._values + tuple(other)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, key):
        result = self._values[key]
        if not isinstance(key, slice):
            return result
        # Negative-step slices lose ascending time order and must be validated.
        if key.step is not None and key.step < 0:
            return ValidatedBars(result)
        child = object.__new__(ValidatedBars)
        object.__setattr__(child, '_values', result)
        return child
