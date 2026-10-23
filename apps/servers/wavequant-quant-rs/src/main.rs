fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    wavequant_quant_rs::presentation::cli::run(std::env::args_os().skip(1))
}
