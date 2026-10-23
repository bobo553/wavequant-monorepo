use wavequant_quant_rs::modules::market_data::{Bar, BarSource, inspect};

struct MemoryBars(Vec<Bar>);

impl BarSource for MemoryBars {
    fn load(&self) -> Result<Vec<Bar>, Box<dyn std::error::Error + Send + Sync>> {
        Ok(self.0.clone())
    }
}

fn bar(timestamp_ms: u64) -> Bar {
    Bar {
        timestamp_ms,
        open_ticks: 100,
        high_ticks: 110,
        low_ticks: 90,
        close_ticks: 105,
        volume: 0,
    }
}

#[test]
fn accepts_chronological_series_with_zero_volume()
-> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let report = inspect(&MemoryBars(vec![bar(1), bar(2)]))?;
    assert_eq!(
        (
            report.bars,
            report.first_timestamp_ms,
            report.last_timestamp_ms
        ),
        (2, 1, 2)
    );
    Ok(())
}

#[test]
fn rejects_empty_duplicate_and_reversed_series() {
    for bars in [vec![], vec![bar(1), bar(1)], vec![bar(2), bar(1)]] {
        assert!(inspect(&MemoryBars(bars)).is_err());
    }
}

#[test]
fn rejects_invalid_ohlc_and_zero_timestamp() {
    let invalid_bars = [
        bar(0),
        Bar {
            low_ticks: 0,
            ..bar(1)
        },
        Bar {
            close_ticks: 120,
            ..bar(1)
        },
        Bar {
            open_ticks: 80,
            ..bar(1)
        },
        Bar {
            low_ticks: 111,
            ..bar(1)
        },
    ];
    for invalid_bar in invalid_bars {
        assert!(inspect(&MemoryBars(vec![invalid_bar])).is_err());
    }
}
