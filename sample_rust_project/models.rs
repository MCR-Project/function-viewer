/// A summary produced at the end of a pipeline run.
pub struct Report {
    kind: String,
    mean: f64,
}

impl Report {
    pub fn new(kind: &str, mean: f64) -> Self {
        Self { kind: kind.to_string(), mean }
    }

    /// One-line human readable summary.
    pub fn summary(&self) -> String {
        format!("{}: {:.2}", self.kind, self.mean)
    }

    /// Verbose description built on top of `summary`.
    pub fn describe(&self) -> String {
        let base = self.summary();
        format!("Report -> {}", base)
    }
}
