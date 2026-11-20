//! A text that must not leak: the device's tunnel config carries its private key.

use serde::{Deserialize, Serialize};
use zeroize::{Zeroize, ZeroizeOnDrop};

/// Held in memory only as long as needed, wiped when dropped, never printed.
#[derive(Clone, PartialEq, Eq, Zeroize, ZeroizeOnDrop, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Secret(String);

impl Secret {
    pub fn new(text: String) -> Self {
        Self(text)
    }

    /// The text itself, for the one place that needs it (the tunnel, the store).
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for Secret {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("<hidden>")
    }
}
