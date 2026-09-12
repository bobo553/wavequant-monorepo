"""Define versioned research profiles while preserving sealed historical profiles."""
from copy import deepcopy
from dataclasses import asdict
from .integrated_strategy import SystemStrategy

PROFILE_ID='lecture_v1'
HIERARCHICAL_PROFILE_ID='lecture_v2'
WAVE_PROFILES={'lecture_v3':(.5,1/3),'lecture_v3_d67_c33':(2/3,1/3),
               'lecture_v3_d50_c50':(.5,.5),'lecture_v3_d67_c50':(2/3,.5)}


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
    deep,shallow=WAVE_PROFILES[variant]
    config=hierarchical_profile(legacy)
    config['strategy'].update(buy_point_definition='whole_flip_wave_v3',first_pullback_threshold=deep,mature_shallow_ratio=shallow)
    config['profile_version']='whole_flip_wave_v3_'+variant
    config['definition'].update(alternation='confirmed_low_at_or_above_whole_flip_origin',
        first_buy='(H0-A)/(H0-L0)>deep_then_positive_n_then_squeeze',
        second_buy='maturity_then_H1_then_pullback_(H1-min_close)/(H1-L0)<=shallow_then_positive_n_then_squeeze',
        first_threshold=deep,second_threshold=shallow,first_class_frozen_at_n_attack=True,
        primary_filters=['level_ge_1_transition','squeeze_regime','whole_wave_ratios','rvol_1_2','gross_rr_1_5','next_open_net_rr_1_5'])
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
