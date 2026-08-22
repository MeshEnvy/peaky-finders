//! Peaky CLI — serve and find.

mod find;

use anyhow::Result;
use clap::{Parser, Subcommand};
use peaky_serve::run_server;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "peaky", version, about = "Peaky Finders — LoRa mesh site planner")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the local web UI for one project
    Serve {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        #[arg(long, default_value = "0.0.0.0")]
        host: String,
        #[arg(short, long, default_value_t = 8080)]
        port: u16,
        #[arg(long)]
        verbose: bool,
        /// Skip land validation, refresh, and cache warm at startup
        #[arg(long, alias = "no-land-refresh")]
        fast_boot: bool,
    },
    /// Auto-find RF chain along a route
    Find {
        #[command(subcommand)]
        command: FindCommands,
    },
}

#[derive(Subcommand)]
enum FindCommands {
    /// Solve minimum-site RF chain covering route waypoints
    Path {
        /// Project directory (or path to config.yaml)
        #[arg(long)]
        project: PathBuf,
        #[arg(long)]
        route: PathBuf,
        #[arg(long)]
        name_prefix: String,
        #[arg(long)]
        tag: Vec<String>,
        #[arg(long, default_value = "installed")]
        allow_tag: Vec<String>,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        quiet: bool,
        #[arg(long, default_value_t = 0.0)]
        simplify_m: f64,
        /// Live map + SSE progress on localhost
        #[arg(long)]
        watch: bool,
        /// Watch server port (default 9847)
        #[arg(long)]
        watch_port: Option<u16>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "peaky=info,tower_http=info".into()),
        )
        .init();

    let cli = Cli::parse();
    match cli.command {
        Commands::Serve {
            project,
            host,
            port,
            verbose,
            fast_boot,
        } => {
            run_server(&host, port, verbose, &project, !fast_boot).await?;
        }
        Commands::Find {
            command:
                FindCommands::Path {
                    project,
                    route,
                    name_prefix,
                    tag,
                    allow_tag,
                    dry_run,
                    quiet,
                    simplify_m,
                    watch,
                    watch_port,
                },
        } => {
            find::run_path(
                project.as_path(),
                &route,
                &name_prefix,
                &tag,
                &allow_tag,
                dry_run,
                quiet,
                simplify_m,
                watch,
                watch_port,
            )
            .await?;
        }
    }
    Ok(())
}
