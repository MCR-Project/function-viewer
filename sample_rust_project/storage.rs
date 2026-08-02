use crate::models::Report;

/// Writes a report to disk (stubbed for the demo).
pub fn save_report(report: &Report) {
    println!("saving {}", report.summary());
}
