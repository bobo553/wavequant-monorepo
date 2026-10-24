use std::{error::Error, future::IntoFuture, time::Duration};
use wavequant_api_rs::{
    bootstrap::{Config, shutdown_signal},
    presentation,
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.iter().any(|argument| matches!(argument.as_str(), "-h" | "--help")) {
        print!("{}", Config::help());
        return Ok(());
    }
    tracing_subscriber::fmt().json().try_init()?;
    let config = Config::from_args(arguments)?;
    if config.check_infrastructure || config.init_database {
        let infrastructure = wavequant_api_rs::application::infrastructure::Infrastructure::from_env()
            .map_err(|error| std::io::Error::other(error.2))?;
        if config.init_database {
            tokio::task::spawn_blocking(move || infrastructure.initialize().map_err(|error| std::io::Error::other(error.2))).await??;
            println!("WaveQuant database schema initialized.");
        } else {
            let health = tokio::task::spawn_blocking(move || infrastructure.health()).await?;
            println!("{}", wavequant_quant_rs::indexed_json::to_string(&health)?);
            if health["status"] == "degraded" { return Err(std::io::Error::other("infrastructure degraded").into()); }
        }
        return Ok(());
    }
    if config.action.is_some() {return wavequant_api_rs::bootstrap::commands::run(config).await;}
    let listener = tokio::net::TcpListener::bind(config.listen).await?;
    tracing::info!(address = %config.listen, "starting HTTP service");

    let state = presentation::initialize(config).await.map_err(|error| std::io::Error::other(error.2))?;
    let jobs = state.jobs.clone();
    let refresh = state.watchlist_refresh.clone();
    let (stop_sender, stop_receiver) = tokio::sync::oneshot::channel::<()>();
    let server = axum::serve(listener, presentation::router(state))
        .with_graceful_shutdown(async {
            let _ = stop_receiver.await;
        })
        .into_future();
    tokio::pin!(server);
    tokio::select! {
        result = &mut server => result?,
        () = shutdown_signal() => {
            if let Some(refresh) = &refresh { refresh.close(); }
            jobs.cancel_for_shutdown().map_err(|error| std::io::Error::other(error.2))?;
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
