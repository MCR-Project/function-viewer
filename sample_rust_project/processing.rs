/// Drops readings that aren't finite.
pub fn clean(readings: &[f64]) -> Vec<f64> {
    readings.iter().copied().filter(|v| v.is_finite()).collect()
}

/// Mean of the cleaned readings.
pub fn aggregate(readings: &[f64]) -> f64 {
    let sum: f64 = readings.iter().sum();
    sum / readings.len() as f64
}
