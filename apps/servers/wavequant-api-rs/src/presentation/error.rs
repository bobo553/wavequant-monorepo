use crate::presentation::indexed_response::Json;
use axum::{ http::StatusCode, response::{IntoResponse, Response}};
use wavequant_quant_rs::indexed_json::json;
use crate::storage::watchlists::StoreError;

#[cfg_attr(test, derive(Debug))]
pub struct ApiError(pub StatusCode, pub &'static str, pub String);
impl ApiError {
    pub fn invalid(message: impl Into<String>) -> Self { Self(StatusCode::BAD_REQUEST, "INVALID_ARGUMENT", message.into()) }
    pub fn unavailable(message: impl Into<String>) -> Self { Self(StatusCode::SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", message.into()) }
    pub fn internal() -> Self { Self(StatusCode::INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "服务暂不可用".into()) }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.2,"code":self.1}))).into_response()
    }
}
impl From<StoreError> for ApiError {
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::Invalid(message) => Self::invalid(message),
            StoreError::Conflict => Self(StatusCode::CONFLICT,"WATCHLIST_CONFLICT","自选已被其他页面修改，请重新加载".into()),
            StoreError::Storage(_) => Self::unavailable("服务器自选存储暂不可用"),
        }
    }
}
impl From<std::io::Error> for ApiError {
    fn from(_: std::io::Error) -> Self { Self::unavailable("所需数据文件不可用") }
}
impl From<wavequant_quant_rs::indexed_json::Error> for ApiError {
    fn from(_: wavequant_quant_rs::indexed_json::Error) -> Self { Self::invalid("JSON 数据格式无效") }
}
impl From<rusqlite::Error> for ApiError {
    fn from(_: rusqlite::Error) -> Self { Self::unavailable("研究数据库暂不可用") }
}
impl From<csv::Error> for ApiError {
    fn from(_: csv::Error) -> Self { Self::invalid("封存 CSV 数据格式无效") }
}
