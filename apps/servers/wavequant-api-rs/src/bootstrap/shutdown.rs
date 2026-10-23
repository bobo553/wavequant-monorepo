/// Windows 使用 Ctrl+C，Unix 同时处理部署环境的 SIGTERM。
pub async fn shutdown_signal() {
    let interrupt = async {
        if let Err(error) = tokio::signal::ctrl_c().await {
            tracing::error!(%error, "failed to register interrupt signal");
        }
    };

    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};
        match signal(SignalKind::terminate()) {
            Ok(mut terminate) => {
                tokio::select! {
                    () = interrupt => {},
                    _ = terminate.recv() => {},
                }
            }
            Err(error) => tracing::error!(%error, "failed to register termination signal"),
        }
    }
    #[cfg(not(unix))]
    interrupt.await;

    tracing::info!("stopping HTTP service");
}
