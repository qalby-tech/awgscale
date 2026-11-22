//! The invitation, as the page /i/<secret> uses it (panel/routes/invites.py):
//! what the link is for, then a named device in exchange for it.

use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::error::Error;
use crate::join::JoinLink;
use crate::secret::Secret;

/// The header the panel wants on a redeem: a form of another site cannot set it.
pub const INVITE_HEADER: &str = "X-Invite";
pub const INVITE_HEADER_VALUE: &str = "1";
const DEVICE_NAME_MAX_LENGTH: usize = 32;
const TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);
/// The server makes the user and starts their tunnel before it answers.
const REDEEM_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(420);

/// What the link is good for.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct InvitationInfo {
    /// The group a device joins; None is no group.
    pub group: Option<String>,
    /// How many more devices may use the link.
    pub left: u32,
    /// For how many devices it was made.
    pub total: u32,
    /// Until when it works, Unix seconds; None is no term.
    pub expires_at: Option<i64>,
}

/// What a device gets for the link.
#[derive(Debug, Clone, Deserialize)]
pub struct Redeemed {
    /// The user made for the device.
    pub name: String,
    /// The device's tunnel config, with its private key.
    pub conf: Secret,
    /// The group it joined; None is no group.
    #[serde(default)]
    pub group: Option<String>,
    /// The device became an owner of the network.
    #[serde(default)]
    pub owner: bool,
}

#[derive(Deserialize)]
struct Refusal {
    #[serde(default)]
    error: Option<String>,
}

/// How the device calls itself: what the panel accepts as a user's name.
pub fn is_device_name(text: &str) -> bool {
    let bytes = text.as_bytes();
    let Some(first) = bytes.first() else {
        return false;
    };
    bytes.len() <= DEVICE_NAME_MAX_LENGTH
        && (first.is_ascii_alphanumeric() || *first == b'_')
        && bytes.iter().all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_' || *byte == b'-')
}

/// Talks to the panel of the network a link names.
pub struct InvitationClient {
    http: reqwest::Client,
    base: Url,
    code: String,
}

impl InvitationClient {
    /// The panel at https://<the link's domain>.
    pub fn new(link: &JoinLink) -> Result<Self, Error> {
        let base = Url::parse(&format!("https://{}/", link.host())).map_err(|_| Error::BadLink)?;
        Self::at(base, link.code())
    }

    /// The panel at another address: for tests against a stand-in.
    pub fn at(base: Url, code: &str) -> Result<Self, Error> {
        let http = reqwest::Client::builder()
            .timeout(TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| Error::Unreachable)?;
        Ok(Self { http, base, code: code.to_owned() })
    }

    fn address(&self, rest: &str) -> Result<Url, Error> {
        self.base.join(&format!("i/{}{}", self.code, rest)).map_err(|_| Error::BadLink)
    }

    /// What the link is for; an error when it no longer works.
    pub async fn info(&self) -> Result<InvitationInfo, Error> {
        let answer = self.http.get(self.address("/info")?).send().await.map_err(|_| Error::Unreachable)?;
        read(answer).await
    }

    /// A user for this device, called `device_name`.
    pub async fn redeem(&self, device_name: &str) -> Result<Redeemed, Error> {
        if !is_device_name(device_name) {
            return Err(Error::Refused(crate::lang::say(
                "Имя: латинские буквы, цифры, _ и -, до 32 символов",
                "A name: Latin letters, digits, _ and -, up to 32 characters",
            )));
        }
        let answer = self
            .http
            .post(self.address("")?)
            .header(INVITE_HEADER, INVITE_HEADER_VALUE)
            .timeout(REDEEM_TIMEOUT)
            .json(&serde_json::json!({ "name": device_name }))
            .send()
            .await
            .map_err(|_| Error::Unreachable)?;
        read(answer).await
    }
}

async fn read<T: for<'de> Deserialize<'de>>(answer: reqwest::Response) -> Result<T, Error> {
    let status = answer.status();
    if status == StatusCode::NOT_FOUND {
        return Err(Error::InvitationGone);
    }
    if !status.is_success() {
        let refusal: Refusal = answer.json().await.map_err(|_| Error::BadAnswer)?;
        return Err(refusal.error.map(Error::Refused).unwrap_or(Error::BadAnswer));
    }
    answer.json().await.map_err(|_| Error::BadAnswer)
}

#[cfg(test)]
mod tests {
    use super::is_device_name;

    #[test]
    fn device_names_as_the_panel_takes_them() {
        for good in ["phone", "anna_phone", "_x", "a-b-c", "A1", "a2345678901234567890123456789012"] {
            assert!(is_device_name(good), "{good}");
        }
        for bad in ["", "-phone", "anna phone", "телефон", "a.b", "a23456789012345678901234567890123"] {
            assert!(!is_device_name(bad), "{bad}");
        }
    }
}
