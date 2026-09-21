"""Define versioned research profiles while preserving sealed historical profiles."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from .integrated_strategy import SystemStrategy

PROFILE_ID='lecture_v1'
HIERARCHICAL_PROFILE_ID='lecture_v2'


@dataclass(frozen=True)
class WaveThresholds:
    first: float | None
    second: float
    first_basis: str = 'alternation_low'
    second_inclusive: bool = True


WAVE_PROFILES={
    'lecture_v3':WaveThresholds(None,1/3),
    'lecture_v3_d67_c33':WaveThresholds(2/3,1/3),
    'lecture_v3_d50_c50':WaveThresholds(.5,.5),
    'lecture_v3_d67_c50':WaveThresholds(2/3,.5),
    'lecture_v3_close_d50_c50':WaveThresholds(.5,.5,'minimum_close',False),
}


def hierarchical_profile(legacy):
    config=research_profile(legacy)
    config['strategy']=asdict(SystemStrategy(pivot_mode='lecture_causal',
        entry_policy='hierarchical_two_buy_points'))
    config['profile_version']='lecture_hierarchical_two_buy_points_v2'
    config['definition'].update(
        entry_policy='level_ge_1_alternation_then_n_squeeze_two_routes',
        channels=['transition_squeeze','mature_shallow_squeeze'],
        preferred_channel='mature_shallow_squeeze',
        trend_levels=[1,2,3], level_combination='any_not_all',
        first_buy_trend_levels=[2,3],
        alternation='confirmed_higher_low_above_flip_origin_no_fraction_filter',
        maturity='later_daily_close_above_frozen_flip_high_after_alternation_known',
        first_buy='alternation_then_new_n_then_squeeze_no_counter_ratio_filter',
        second_buy='maturity_then_new_up_leg_shallow_pullback_below_1_3_then_new_n_then_squeeze',
        no_same_level_fallback_after_maturity=True,
        primary_filters=['level_ge_1_transition','squeeze_regime','type2_only_countermove_below_1_3',
                         'rvol_1_2','gross_rr_1_5','next_open_net_rr_1_5'],
        context_only=['washout','three_six_fraction_strength'],
        drawing_annotations='unchanged_legacy_drawing_not_v2_entry_evidence')
    return config


def whole_wave_profile(legacy,variant='lecture_v3'):
    thresholds=WAVE_PROFILES[variant]
    config=hierarchical_profile(legacy)
    for scenario in config['scenarios'].values():
        scenario['execution'].update(staged_exit_enabled=True, staged_exit_same_day=False,
                                     staged_exit_intraday=True, missing_minute_daily_fallback=True, inverse_n_after_reduction=True, inverse_n_close_reduce=True, initial_reduction_fraction=0.65,
                                     exit_on_target=False, volume_inverse_n_clear=True, volume_down_exit=True, small_n_reduction=True)
    config['strategy'].update(buy_point_definition='whole_flip_wave_v3',preflight_reward_risk=False,
        first_pullback_threshold=thresholds.first,mature_shallow_ratio=thresholds.second,
        first_pullback_basis=thresholds.first_basis,mature_shallow_inclusive=thresholds.second_inclusive)
    config['profile_version']='whole_flip_wave_v3_'+variant
    config['definition'].update(alternation='confirmed_low_at_or_above_whole_flip_origin',
        first_buy=('level_2_or_3_confirmed_alternation_then_new_positive_n_then_squeeze'
                   if thresholds.first is None else
                   '(H0-min_close_H0_to_A)/(H0-L0)>deep_then_positive_n_then_squeeze'
                   if thresholds.first_basis=='minimum_close' else
                   '(H0-A)/(H0-L0)>deep_then_positive_n_then_squeeze'),
        second_buy=('maturity_then_H1_then_pullback_(H1-min_close)/(H1-L0)<shallow_then_positive_n_then_squeeze'
                    if not thresholds.second_inclusive else
                    'maturity_then_H1_then_pullback_(H1-min_close)/(H1-L0)<=shallow_then_positive_n_then_squeeze'),
        first_threshold=thresholds.first,second_threshold=thresholds.second,first_class_frozen_at_n_attack=True,
        primary_filters=['level_ge_1_transition','squeeze_regime','whole_wave_ratios','rvol_1_2','gross_rr_1_5','next_open_net_rr_1_5'])
    if thresholds.first is None:
        config['profile_version'] = 'confirmed_chart_alternation_v3'
        config['definition'].update(
            alternation='shared_chart_confirmed_higher_pullback_strictly_below_two_thirds',
            first_buy_trend_levels=[2,3],
            confirmation='formal_flip_high_and_confirmed_source_pullback_may_be_known_together',
            primary_filters=['first_buy_level_2_or_3_alternation','squeeze_regime','type2_whole_wave_ratio',
                             'rvol_1_2','gross_rr_1_5','next_open_net_rr_1_5'])
    config['profile_version'] = 'volume_inverse_n_v21_' + variant
    config['definition']['exits'] = [
        rule for rule in config['definition']['exits'] if rule != 'target_observed_then_next_open'
    ]
    config['definition']['primary_filters'] = [
        name for name in config['definition']['primary_filters'] if name != 'gross_rr_1_5'
    ]
    config['definition']['primary_filters'] += [
        'attack_volume_strictly_above_previous', 'bullish_attack_body_ge_2pct_open_and_half_range',
        'alternation_confirmed_before_n_attack',
    ]
    config['definition'].update(
        signal_timing='qualified_positive_n_squeeze_confirmation_close',
        reward_risk_policy='next_open_execution_gate_only_not_signal_preflight',
        alternation='shared_chart_formal_or_qualified_b_positive_n_squeeze',
        staged_exit='verified_5m_closing_window_low_break_35_low_and_price_break_65_next_interval_open_then_weak_rebound_clear',
        missing_minute_policy='same_source_daily_close_with_explicit_fallback_evidence',
        inverse_n_after_reduction='cumulative_90_percent_of_initial_holding_other_full_risk_exits_take_priority',
        small_n_reduction='body_le_1pct_and_lt_previous_10_mean_inside_latest_valid_confirmed_n_candle_then_cumulative_30',
        volume_down_exit='volume_gt_previous_bearish_close_below_previous_then_next_open_cumulative_70_frozen_support_low_break_close_clear',
        volume_inverse_n_clear='confirmed_inverse_n_and_volume_gt_previous_same_day_full_clear_before_partial_exits',
        inverse_n_remaining_exit='bull_resistance_next_close_below_virtual_low_same_day_clear',
        inverse_n_close_reduce='confirmed_inverse_n_same_day_close_cumulative_90_without_prior_reduction',
        confirmation='qualified_b_then_positive_n_squeeze_or_existing_formal_confirmation',
        alternation_price_path='b_low>=a_high-(a_high-a_low)*2/3',
        alternation_time_path='b_duration>a_duration_and_b_min_close<a_high-(a_high-a_low)/2',
        alternation_invalidation='b_low_broken_before_close_above_a_high',
        confirming_n_can_enter=False,
        minimum_attack_body_open_fraction=0.02,
        minimum_attack_body_range_fraction=0.5,
        attack_volume_vs_previous='strictly_greater',
        drawing_annotations='shared_causal_alternation_evidence',
        target_policy='nearest_unhit_n_target_then_confirmed_two_t_wave_projection',
        target_exit_policy='measured_targets_are_milestones_not_exit_orders',
        wave_projection=dict(
            activation='qualified_positive_n_squeeze_and_two_t_reached_with_defense_held',
            stacking='叠箱五顶：box_anchor+5*(box_anchor-origin)',
            pushing='堆箱五顶：B_low+3*(box_anchor-origin)',
            ten_full='五顶满足后，以已观察五顶段高点-origin放大；叠箱从高点加，堆箱从新B低加',
            invalidation='strict_low_below_frozen_squeeze_defense',
            role='conditional_targets_not_automatic_orders_or_elliott_wave_count',
        ),
    )

    return config


def research_profile(legacy):
    # Inherit execution assumptions only, NEVER the sealed strategy's metrics,
    # folds, bootstrap confidence intervals or validation pass/fail flags.
    config={'scenarios':{name:{'execution':deepcopy(value['execution'])}
                         for name,value in legacy['scenarios'].items()}}
    config['strategy']=asdict(SystemStrategy(pivot_mode='lecture_causal'))
    config['strategy'].pop('mature_shallow_ratio')  # V1 definition remains byte-for-byte compatible.
    config['strategy'].pop('buy_point_definition')
    config['strategy'].pop('first_pullback_threshold')
    config['strategy'].pop('first_pullback_basis')
    config['strategy'].pop('mature_shallow_inclusive')
    config['profile_version']='lecture_causal_squeeze_v1'
    config['definition']=dict(
        structure='same_lecture_reducer_close_confirmed_only',
        entry_policy='bear_flip_then_alternation_then_bull_then_new_n_squeeze',
        channels=['n_continuation','squeeze_pullback_resume'],
        conditional_tags=dict(washout_reattack='annotation_only_all_entry_gates_still_required',
                              positive_turn_then_n='requires_caller_supplied_minor_points_unavailable_in_daily_dashboard'),
        primary_filters=['bullish_transition','squeeze_regime','countermove_below_2_3','rvol_1_2','gross_rr_1_5','next_open_net_rr_1_5'],
        context_only=['level_1_2_3_trends','washout','three_six_fraction_strength'],
        not_entry_gates=['abc_equal_wave','five_tops_ten_full','claimed_70_or_80_percent_probabilities'],
        target_policy='nearest_unhit_equal_wave_then_one_p_then_two_t_no_skipping_to_inflate_rr',
        exits=['inverse_n','last_rise_low_close_break','undefined_structure',
               'stop_observed_then_next_open','target_observed_then_next_open','maximum_holding_bars'],
        conditional_exits=dict(negative_turn='requires_caller_supplied_minor_points_unavailable_in_daily_dashboard'),
        limitations=['child_mother_order_is_a_lecture_convention_not_observed_intrabar_path',
                     'same_day_pivots_do_not_form_a_daily_N','no_profitability_claim'])
    return config
