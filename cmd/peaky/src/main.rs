//! Peaky CLI — serve, find, and freeze.

mod find;
mod freeze;
mod peaks;

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
    /// Build eligible-peaks catalog (peaks.yaml)
    Peaks {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Scan bbox west,south,east,north (default: project AOI)
        #[arg(
            long,
            value_name = "W,S,E,N",
            allow_hyphen_values = true,
            conflicts_with_all = ["polygon", "corridor", "corridor_sites"]
        )]
        bbox: Option<String>,
        /// Clip scan to a GeoJSON polygon file
        #[arg(long, value_name = "PATH", conflicts_with_all = ["bbox", "corridor", "corridor_sites"])]
        polygon: Option<PathBuf>,
        /// Corridor from_lat,from_lon,to_lat,to_lon (100 mi wide by default)
        #[arg(
            long,
            value_name = "LAT,LON,LAT,LON",
            allow_hyphen_values = true,
            conflicts_with_all = ["bbox", "polygon", "corridor_sites"]
        )]
        corridor: Option<String>,
        /// Corridor from site slug to site slug (e.g. tonopah-overlook,eip-us1041015)
        #[arg(
            long,
            value_name = "FROM,TO",
            conflicts_with_all = ["bbox", "polygon", "corridor"]
        )]
        corridor_sites: Option<String>,
        /// Total corridor width in miles (default 100)
        #[arg(long, default_value_t = 100.0)]
        corridor_width_mi: f64,
        /// Re-download OSM PBF even if cached
        #[arg(long)]
        force: bool,
        #[arg(long)]
        verbose: bool,
    },
    /// Export a compact official project base (config.yaml + project.geojson)
    Freeze {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Output directory (default: <project>/{slug}-base-{date}/)
        #[arg(short, long, value_name = "DIR")]
        output: Option<PathBuf>,
        /// Include sites in config.yaml
        #[arg(long)]
        include_sites: bool,
        #[arg(long)]
        verbose: bool,
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
                .unwrap_or_else(|_| {
                    "peaky=info,peaky_peaks=info,peaky_geo=info,tower_http=info".into()
                }),
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
        Commands::Peaks {
            project,
            bbox,
            polygon,
            corridor,
            corridor_sites,
            corridor_width_mi,
            force,
            verbose,
        } => {
            peaks::run(
                project,
                bbox,
                polygon,
                corridor,
                corridor_sites,
                corridor_width_mi,
                force,
                verbose,
            )?;
        }
        Commands::Freeze {
            project,
            output,
            include_sites,
            verbose,
        } => {
            freeze::run(project, output, include_sites, verbose)?;
        }
    }
    Ok(())
}
