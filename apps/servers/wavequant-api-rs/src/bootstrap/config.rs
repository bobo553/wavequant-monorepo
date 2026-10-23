use std::{io, net::SocketAddr};

/// 本地研究服务只接受回环地址；部署暴露需要单独设计认证边界。
pub struct Config {
    pub listen: SocketAddr,
}

impl Config {
    pub fn from_args(args: impl IntoIterator<Item = String>) -> io::Result<Self> {
        let mut args = args.into_iter();
        let listen = match args.next().as_deref() {
            None => "127.0.0.1:8010".parse::<SocketAddr>(),
            Some("--listen") => args
                .next()
                .ok_or_else(|| invalid_input("--listen requires an IP:port value"))?
                .parse::<SocketAddr>(),
            Some(_) => return Err(invalid_input("usage: wavequant-api-rs [--listen IP:port]")),
        }
        .map_err(|_| invalid_input("invalid listen address"))?;
        if args.next().is_some() || !listen.ip().is_loopback() || listen.port() == 0 {
            return Err(invalid_input(
                "require a loopback address and a port from 1 to 65535",
            ));
        }
        Ok(Self { listen })
    }
}

fn invalid_input(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}
