use crate::presentation::indexed_response::Json;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::time::Duration;
use axum::{ extract::State, http::StatusCode};
use wavequant_quant_rs::analysis::analyzer::{AnalysisRequest, AnalysisResponse, analyze_with_cancel};
use super::{AppState, error::ApiError};

struct CancelOnDrop(Arc<AtomicBool>);
impl Drop for CancelOnDrop {
    fn drop(&mut self) { self.0.store(true, Ordering::Relaxed); }
}

pub async fn analyze(
    State(state): State<AppState>,
    Json(request): Json<AnalysisRequest>,
) -> Result<Json<AnalysisResponse>, ApiError> {
    let permit = state.workers.clone().try_acquire_owned()
        .map_err(|_| ApiError(StatusCode::TOO_MANY_REQUESTS, "ANALYSIS_BUSY", "分析任务已达并发上限".into()))?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let _cancellation = CancelOnDrop(cancelled.clone());
    let job = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        analyze_with_cancel(request, &cancelled)
    });
    match tokio::time::timeout(Duration::from_secs(60), job).await {
        Ok(Ok(Ok(response))) => Ok(Json(response)),
        Ok(Ok(Err(message))) => Err(ApiError::invalid(message)),
        Ok(Err(error)) => {
            tracing::error!(%error, "analysis worker failed");
            Err(ApiError::internal())
        }
        Err(_) => Err(ApiError(StatusCode::GATEWAY_TIMEOUT, "ANALYSIS_TIMEOUT", "分析超时，任务正在取消".into())),
    }
}
