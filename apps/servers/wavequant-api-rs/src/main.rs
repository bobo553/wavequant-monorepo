use std::{error::Error, future::IntoFuture, time::Duration};
use wavequant_api_rs::{
    bootstrap::{Config, shutdown_signal},
    presentation,
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    tracing_subscriber::fmt().json().try_init()?;
    let config = Config::from_args(std::env::args().skip(1))?;
    let listener = tokio::net::TcpListener::bind(config.listen).await?;
    tracing::info!(address = %config.listen, "starting HTTP service");

    let (stop_sender, stop_receiver) = tokio::sync::oneshot::channel::<()>();
    let server = axum::serve(listener, presentation::router())
        .with_graceful_shutdown(async {
            let _ = stop_receiver.await;
        })
        .into_future();
    tokio::pin!(server);
    tokio::select! {
        result = &mut server => result?,
        () = shutdown_signal() => {
            let _ = stop_sender.send(());
            match tokio::time::timeout(Duration::from_secs(10), &mut server).await {
                Ok(result) => result?,
                Err(_) => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "HTTP shutdown exceeded 10 seconds",
                    ).into());
                }
            }
        }
    }
    Ok(())
}
