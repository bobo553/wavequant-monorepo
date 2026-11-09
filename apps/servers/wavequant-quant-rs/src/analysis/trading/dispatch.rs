//! 日信号调度参考 Python consume_signal_day；资格、信号历史和账户持仓各自独立。
use serde::{Deserialize, Serialize};

use super::qualification::TradingEligibility;

#[derive(Debug, Clone, Copy)]
pub struct DecisionScope {
    pub scan_allowed: bool,
    pub buy_allowed: bool,
}

impl DecisionScope {
    pub fn at(eligibility: &TradingEligibility, now: usize, unrestricted: bool) -> Self {
        // 结束清仓属于账户边界处理；普通买卖计算严格限制在 [start, end)。
        Self {
            scan_allowed: unrestricted || eligibility.contains(now),
            buy_allowed: unrestricted || eligibility.contains(now),
        }
    }
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct SignalHistory {
    has_buy_signal: bool,
}

impl SignalHistory {
    pub fn observes_exits(&self, scope: DecisionScope) -> bool {
        scope.scan_allowed
    }

    pub fn permits_entry(&self, scope: DecisionScope, has_exit: bool) -> bool {
        scope.scan_allowed && scope.buy_allowed && !has_exit
    }

    pub fn record_buy_signal(&mut self) {
        // 信号历史不授予区间外继续扫描的权限；未成交退出由账户维护。
        self.has_buy_signal = true;
    }
}
