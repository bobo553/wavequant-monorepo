from datetime import datetime

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def test_prior_high_supplies_distinct_inverse_n_origin_before_mother_candle():
    rows = [
        ("2026-04-24", 10.37438773936516, 10.670064217686596, 10.181555253503355, 10.631497720514234, 6221700.0),
        ("2026-04-27", 10.605786722399326, 11.081440187525114, 10.37438773936516, 10.99145169412294, 9461201.0),
        ("2026-04-28", 10.901463200720764, 11.0300181912953, 10.734341712973864, 10.92717419883567, 7153342.0),
        ("2026-04-29", 10.88860770166331, 11.158573181869837, 10.78576370920368, 10.978596195065483, 5466516.0),
        ("2026-04-30", 11.004307193180393, 11.569949151708357, 10.92717419883567, 11.454249660191273, 9855042.0),
        ("2026-05-06", 11.68564864322544, 12.418412089500302, 11.467105159248726, 12.34127909515558, 21613000.0),
        ("2026-05-07", 12.23843510269595, 13.035476044258083, 12.212724104581044, 12.778366063109006, 23689300.0),
        ("2026-05-08", 12.68837756970683, 13.09975353954535, 12.431267588557757, 12.919776552741, 17061574.0),
        ("2026-05-11", 12.95834304991336, 13.215453031062433, 12.7526550649941, 12.919776552741, 14438928.0),
        ("2026-05-12", 12.881210055568637, 13.241164029177343, 12.868354556511184, 13.16403103483262, 14424895.0),
        ("2026-05-13", 13.40828551692424, 14.4752919386929, 13.36971901975188, 13.871083482992573, 31107924.0),
        ("2026-05-14", 13.498274010326416, 15.015222899105956, 13.498274010326416, 14.655268925497252, 27193358.0),
        ("2026-05-15", 14.92523440570378, 15.78655284255318, 14.655268925497252, 15.413743369887023, 24570887.0),
        ("2026-05-18", 15.362321373657206, 15.40088787082957, 14.655268925497252, 14.873812409473965, 18106342.0),
        ("2026-05-19", 14.976656401933596, 15.002367400048502, 14.411014443405632, 14.629557927382345, 16304800.0),
        ("2026-05-20", 14.565280432095076, 14.74525741889943, 14.256748454716186, 14.308170450946003, 14448000.0),
        ("2026-05-21", 14.33388144906091, 15.079500394393225, 13.806805987705307, 13.999638473567112, 14269100.0),
        ("2026-05-22", 14.051060469796926, 14.346736948118362, 13.832516985820213, 14.23103745660128, 14262901.0),
        ("2026-05-25", 14.26960395377364, 15.040933897220864, 14.141048963199102, 14.256748454716186, 13521700.0),
        ("2026-05-26", 14.359592447175816, 14.372447946233269, 13.292586025407157, 13.562551505613685, 14280800.0),
        ("2026-05-27", 13.421141015981693, 13.703961995245676, 12.95834304991336, 13.09975353954535, 9664060.0),
        ("2026-05-28", 13.241164029177343, 13.241164029177343, 12.726944066879193, 12.984054048028266, 8898900.0),
        ("2026-05-29", 13.228308530119888, 13.266875027292249, 11.917047626259606, 12.045602616834143, 14521200.0),
        ("2026-06-01", 11.929903125317061, 12.482689584787572, 11.775637136627617, 12.174157607408683, 15376426.0),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sz.300154", *values) for day, *values in rows]
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
    )
    generated = generate_system_signals(bars, config)
    event = next(
        row
        for row in generated.audit
        if row["event"] == "n_completed" and row["direction"] == "down" and row["timestamp"].startswith("2026-05-26")
    )
    assert bars[event["origin"]].timestamp.date().isoformat() == "2026-05-15"
    assert bars[event["neckline"]].timestamp.date().isoformat() == "2026-05-21"
    assert bars[event["pullback"]].timestamp.date().isoformat() == "2026-05-25"
    cutoff = next(i for i, bar in enumerate(bars) if bar.timestamp.date().isoformat() == "2026-05-26")
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert [s for s in generated.signals if s.bar_index <= cutoff] == prefix.signals

    outside = next(row for row in generated.audit if row["event"] == "n_completed"
                   and row["direction"] == "down" and row["timestamp"].startswith("2026-05-21"))
    assert [bars[outside[key]].timestamp.date().isoformat() for key in ("origin", "neckline", "pullback")] == [
        "2026-05-15", "2026-05-20", "2026-05-21"]
    cutoff = outside["pullback"]
    prefix = generate_system_signals(bars[:cutoff+1], config)
    assert [s for s in generated.signals if s.bar_index <= cutoff] == prefix.signals
    assert any(s.side == "EXIT" and s.bar_index == cutoff and s.reason == "inverse_n_risk_exit" for s in prefix.signals)
