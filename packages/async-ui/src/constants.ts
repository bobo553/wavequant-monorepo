/**
 * 队列内部使用的取消哨兵。
 *
 * 使用冻结对象而不是 Error，可让调用方通过引用相等稳定地区分“用户主动取消”与
 * “资源加载失败”，同时避免把正常控制流误报到异常监控。
 */
export const POPUP_CANCELLED = Object.freeze({
    type: "popup-cancelled",
} as const);

/** 默认加载超时；足够覆盖常规弱网，又不会让界面永久停留在 loading。 */
export const DEFAULT_LOAD_TIMEOUT_MS = 8_000;

/** 默认仅重试一次，避免 Chunk 故障时产生持续请求风暴。 */
export const DEFAULT_LOAD_RETRY = 1;
