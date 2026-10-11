//! Peaky CLI — serve, find, and freeze.

mod coverage;
mod export;
mod find;
mod freeze;
mod map_export;
mod peaks;
mod probe;

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
    /// Eligible-peaks catalog (build or probe)
    Peaks {
        #[command(subcommand)]
        command: PeaksCommands,
    },
    /// Export tag-filtered sites as KMZ (pin, viewshed, access)
    Export {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Include sites that have any of these tags
        #[arg(long, required = true)]
        tag: Vec<String>,
        /// Exclude sites that have any of these tags
        #[arg(long)]
        exclude_tag: Vec<String>,
        /// Output KMZ path (default: {first-tag}.kmz)
        #[arg(short, long, value_name = "PATH")]
        output: Option<PathBuf>,
        #[arg(long)]
        verbose: bool,
    },
    /// Export splatter coverage overlay for meshenvy.org /map
    Map {
        #[command(subcommand)]
        command: MapCommands,
    },
    /// Ensure splatter viewshed PNGs and write a footprint manifest
    Coverage {
        #[command(subcommand)]
        command: CoverageCommands,
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
enum CoverageCommands {
    /// Generate splatter viewsheds for Point features in a GeoJSON file
    Points {
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        #[arg(value_name = "GEOJSON")]
        geojson: PathBuf,
        #[arg(long, default_value_t = 3)]
        quality: u8,
        #[arg(long)]
        workers: Option<usize>,
        #[arg(long, value_name = "PATH")]
        out: PathBuf,
        #[arg(long)]
        verbose: bool,
    },
    /// Generate splatter viewsheds for deployed fleet sites (sites.yaml + nodes.yaml)
    Fleet {
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        #[arg(long)]
        workers: Option<usize>,
        #[arg(long, value_name = "PATH")]
        out: PathBuf,
        #[arg(long)]
        verbose: bool,
    },
}

#[derive(Subcommand)]
enum MapCommands {
    /// Write coverage-network.geojson + coverage-tiles/ from fleet splatter viewsheds
    Export {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Output directory (default: meshenvy.org/static when PEAKY_MAP_OUT is unset)
        #[arg(long, value_name = "DIR")]
        out_dir: Option<PathBuf>,
        #[arg(long)]
        workers: Option<usize>,
        #[arg(long, default_value_t = 0.00045)]
        merge_resolution_deg: f64,
        #[arg(long, default_value_t = 0)]
        close_iterations: u32,
        #[arg(long, default_value_t = 6)]
        tile_zoom_min: u32,
        #[arg(long, default_value_t = 11)]
        tile_zoom_max: u32,
        #[arg(long)]
        tile_workers: Option<u32>,
        #[arg(long)]
        skip_tiles: bool,
        #[arg(long)]
        verbose: bool,
    },
}

#[derive(Subcommand)]
enum PeaksCommands {
    /// Build eligible-peaks index under .peaky/cache/
    Build {
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
        /// Stop after N qualifying peaks (serial scan; prints hike profile for each)
        #[arg(long)]
        stop_after: Option<usize>,
        /// Ignore existing index for this prefs_key (default: merge/freshen)
        #[arg(long)]
        clean: bool,
    },
    /// Remove stale probe/peaks cache dirs for other prefs keys
    Gc {
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Keep this many recent stale dirs in addition to the current prefs key
        #[arg(long, default_value_t = 1)]
        keep: usize,
        #[arg(long)]
        dry_run: bool,
    },
    /// Eligibility report: one lat,lon or every candidate in a scan region
    Probe {
        /// Project directory (or path to config.yaml)
        #[arg(value_name = "PROJECT")]
        project: PathBuf,
        /// Single-point probe (omit when using --corridor / --bbox / --polygon)
        #[arg(value_name = "LAT,LON", allow_hyphen_values = true)]
        lat_lon: Option<String>,
        /// Scan bbox west,south,east,north
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
        /// Corridor from_lat,from_lon,to_lat,to_lon
        #[arg(
            long,
            value_name = "LAT,LON,LAT,LON",
            allow_hyphen_values = true,
            conflicts_with_all = ["bbox", "polygon", "corridor_sites"]
        )]
        corridor: Option<String>,
        /// Corridor from site slug to site slug
        #[arg(
            long,
            value_name = "FROM,TO",
            conflicts_with_all = ["bbox", "polygon", "corridor"]
        )]
        corridor_sites: Option<String>,
        /// Total corridor width in miles (probe default 20)
        #[arg(long, default_value_t = 20.0)]
        corridor_width_mi: f64,
        /// Re-download OSM PBF even if cached
        #[arg(long)]
        force: bool,
        #[arg(long)]
        verbose: bool,
        /// Print scored park candidates (single-point probe only)
        #[arg(long)]
        explain: bool,
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
        Commands::Peaks { command } => match command {
            PeaksCommands::Build {
                project,
                bbox,
                polygon,
                corridor,
                corridor_sites,
                corridor_width_mi,
                force,
                verbose,
                stop_after,
                clean,
            } => {
                peaks::run_build(
                    project,
                    bbox,
                    polygon,
                    corridor,
                    corridor_sites,
                    corridor_width_mi,
                    force,
                    verbose,
                    stop_after,
                    clean,
                )?;
            }
            PeaksCommands::Gc {
                project,
                keep,
                dry_run,
            } => {
                peaks::run_gc(project, keep, dry_run)?;
            }
            PeaksCommands::Probe {
                project,
                lat_lon,
                bbox,
                polygon,
                corridor,
                corridor_sites,
                corridor_width_mi,
                force,
                verbose,
                explain,
            } => {
                peaks::run_probe(
                    project,
                    lat_lon,
                    bbox,
                    polygon,
                    corridor,
                    corridor_sites,
                    corridor_width_mi,
                    force,
                    verbose,
                    explain,
                )?;
            }
        },
        Commands::Export {
            project,
            tag,
            exclude_tag,
            output,
            verbose,
        } => {
            export::run(project, &tag, &exclude_tag, output, verbose)?;
        }
        Commands::Coverage {
            command: CoverageCommands::Points {
                project,
                geojson,
                quality,
                workers,
                out,
                verbose,
            },
        } => {
            let w = workers.unwrap_or_else(default_coverage_workers);
            coverage::run_points(project, geojson, quality, w, out, verbose)?;
        }
        Commands::Coverage {
            command: CoverageCommands::Fleet {
                project,
                workers,
                out,
                verbose,
            },
        } => {
            let w = workers.unwrap_or_else(default_coverage_workers);
            coverage::run_fleet(project, w, out, verbose)?;
        }
        Commands::Map {
            command:
                MapCommands::Export {
                    project,
                    out_dir,
                    workers,
                    merge_resolution_deg,
                    close_iterations,
                    tile_zoom_min,
                    tile_zoom_max,
                    tile_workers,
                    skip_tiles,
                    verbose,
                },
        } => {
            let out = out_dir.unwrap_or_else(default_map_out_dir);
            map_export::run(
                project,
                out,
                workers,
                merge_resolution_deg,
                close_iterations,
                tile_zoom_min,
                tile_zoom_max,
                tile_workers,
                skip_tiles,
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

fn default_coverage_workers() -> usize {
    std::env::var("PEAKY_MAP_WORKERS")
        .ok()
        .and_then(|s| s.parse().ok())
        .filter(|n| *n > 0)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(4)
                .clamp(1, 16)
        })
}

fn default_map_out_dir() -> PathBuf {
    if let Ok(raw) = std::env::var("PEAKY_MAP_OUT") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    PathBuf::from("/Volumes/Code/repos/meshenvy/meshenvy.org/static")
}
