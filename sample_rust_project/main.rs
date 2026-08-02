mod models;
mod processing;
mod storage;

use models::Report;
use storage::save_report;

/// Entry point of the sample pipeline.
fn main() {
    let readings = load_readings();
    let cleaned = processing::clean(&readings);
    let mean = processing::aggregate(&cleaned);
    let report = Report::new("daily", mean);
    save_report(&report);
    println!("{}", report.describe());
}

/// Stand-in for reading a CSV; returns a fixed sample for the demo.
fn load_readings() -> Vec<f64> {
    vec![1.0, 2.0, 3.0, 4.0]
}
