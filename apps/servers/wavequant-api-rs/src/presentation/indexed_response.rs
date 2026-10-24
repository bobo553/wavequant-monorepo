//! HTTP 数据与 Worker/缓存使用同一节点协议；请求仍接受原有 JSON 字段。
use axum::{extract::{FromRequest,Request},response::{IntoResponse,Response}};
use serde::{Serialize,de::DeserializeOwned};
use wavequant_quant_rs::indexed_json::{self,wire};
pub struct Json<T>(pub T);
impl<T:Serialize> IntoResponse for Json<T>{
    fn into_response(self)->Response{
        let value=indexed_json::to_value(self.0).and_then(|v|{
            // 精简DTO已经是业务索引，不能再包装成通用JSON节点表。
            if crate::application::trend_state::is_response(&v)||crate::application::trend_state::is_response(&v["result"]){Ok(v)}else{wire::pack(&v)}
        });
        match value{
            Ok(value)=>{let format=if wire::is_document(&value){wire::FORMAT}else{"wavequant.trend-state.v1"};let mut response=axum::Json(value).into_response();response.headers_mut().insert("x-wavequant-data-format",axum::http::HeaderValue::from_static(format));response},
            Err(error)=>{tracing::error!(error=%error,"indexed response unavailable");(axum::http::StatusCode::INTERNAL_SERVER_ERROR,axum::Json(indexed_json::json!({"error":"响应数据暂不可用","code":"INDEXED_RESPONSE_UNAVAILABLE"}))).into_response()},
        }
    }
}
impl<S:Send+Sync,T:DeserializeOwned> FromRequest<S> for Json<T>{
    type Rejection=Response;
    async fn from_request(request:Request,state:&S)->Result<Self,Self::Rejection>{
        let axum::Json(value)=axum::Json::<indexed_json::Value>::from_request(request,state).await.map_err(IntoResponse::into_response)?;
        indexed_json::from_value(value).map(Self).map_err(|_|super::error::ApiError::invalid("请求字段或索引数据无效").into_response())
    }
}
