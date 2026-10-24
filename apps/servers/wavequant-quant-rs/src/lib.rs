// 完整来源证据含较多字段；仅调整编译期 JSON 宏展开深度，不改变运行期预算。
#![recursion_limit = "256"]

pub mod analysis;
pub mod data;
pub mod execution;
pub mod infrastructure;
pub mod modules;
pub mod presentation;
pub mod persistence;
pub mod governance;
pub mod analytics;

pub mod indexed_json;
