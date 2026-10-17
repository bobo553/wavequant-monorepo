"""普通升降整波结束选项的默认值、严格输入与持久化边界。"""

import unittest

from wavequant_api.server import ordinary_completion_option
from wavequant_api.watchlists import settings


class OrdinaryTrendOptionTests(unittest.TestCase):
    def test_query_requires_one_explicit_boolean_string(self):
        self.assertFalse(ordinary_completion_option({}))
        self.assertFalse(ordinary_completion_option({'ordinary_trend_completion_enabled': ['false']}))
        self.assertTrue(ordinary_completion_option({'ordinary_trend_completion_enabled': ['true']}))
        for values in ([], ['1'], ['True'], ['true', 'false']):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ordinary_completion_option({'ordinary_trend_completion_enabled': values})

    def test_watchlist_context_preserves_selection_and_defaults_old_records_off(self):
        context = dict(run='example', variant='lecture_v3', scenario='base', source='tdx',
                       start='2018-01-01', volume_filter='false', net_reward_risk_filter='false',
                       shallow_base_breakout_enabled='true', initial_capital='100000', max_position_weight='1')
        old = settings(dict(enabled=False, context=context))
        self.assertEqual(old['context']['ordinary_trend_completion_enabled'], 'false')
        for selected in ('false', 'true'):
            result = settings(dict(enabled=False, context=dict(context, ordinary_trend_completion_enabled=selected)))
            self.assertEqual(result['context']['ordinary_trend_completion_enabled'], selected)
        self.assertNotIn('ordinary_trend_completion_enabled', context)
