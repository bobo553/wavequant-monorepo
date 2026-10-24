//! 离线运行编排。阶段必须返回实际原生执行结果，不从未运行的门禁推导通过。
mod operations;
mod platform;
pub use operations::*;
pub use platform::*;
