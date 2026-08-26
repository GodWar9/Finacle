fn main() -> Result<(), Box<dyn std::error::Error>> {
    std::fs::create_dir_all("src/pb")?;
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .out_dir("src/pb")
        .compile(&["proto/ledger.proto"], &["proto"])?;

    println!("cargo:rerun-if-changed=proto/ledger.proto");
    Ok(())
}
