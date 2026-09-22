// Regression fixture for #22: this project has no `src/` layout, so picking
// it directly names its one crate "crate" and every module sits one level
// below the crate root ("crate::a", "crate::b"). Picking *this directory's
// parent* instead - as the app/ prefix here simulates - keeps the same crate
// name but pushes "app" into every module name too ("crate::app::a",
// "crate::app::b"), which is what used to break a bare module path.
mod b;

/// Calls b's function via a bare module path - no `use` needed or written.
pub fn from_a() -> i32 {
    b::from_b()
}
