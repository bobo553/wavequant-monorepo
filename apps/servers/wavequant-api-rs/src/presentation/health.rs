use axum::{Router, http::StatusCode, routing::get};

/// 此探针只证明 HTTP 进程存活，不代表量化任务或外部依赖就绪。
pub fn router() -> Router {
    Router::new().route("/health/live", get(|| async { StatusCode::NO_CONTENT }))
}
