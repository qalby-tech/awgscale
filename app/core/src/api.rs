//! The app API of a network (panel/routes/app.py). It answers only inside the
//! tunnel, at the entry's tunnel address, and knows the device by the address
//! its requests come from: there is nothing to log in with.

use std::net::Ipv4Addr;

use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::error::Error;

/// The port Traefik serves the app API on, inside the tunnel only.
pub const API_PORT: u16 = 8088;
/// The header every change needs: a page of another site cannot send it.
pub const APP_HEADER: &str = "X-Awgscale";
pub const APP_HEADER_VALUE: &str = "1";
const TIMEOUT: std::time::Duration = std::time::Duration::from_secs(20);
/// Moving a user to another exit restarts nothing, but waits for the agent.
const CHANGE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(70);

/// How a group's requests are filtered (GroupFilter in agent/users/models.py).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Filter {
    #[serde(rename = "std")]
    Standard,
    #[serde(rename = "off")]
    Off,
    #[serde(rename = "kids")]
    Kids,
    #[serde(rename = "pro")]
    Pro,
    #[serde(rename = "proplus")]
    ProPlus,
    #[serde(rename = "ultimate")]
    Ultimate,
}

/// Which exit the user is kept on, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExitChoice {
    /// The exit chosen for the user (by them or by the admin); None is not chosen.
    pub pinned: Option<u32>,
    /// The exit of the user's group; None when the group keeps nobody on one.
    pub group: Option<u32>,
    /// The name of the exit that applies; None is as the shares decide.
    pub name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Member {
    pub name: String,
    pub address: Ipv4Addr,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Group {
    pub name: Option<String>,
    pub filter: Filter,
    /// Its members' devices reach one another.
    pub space: bool,
    /// Only when it is a space; otherwise empty.
    pub members: Vec<Member>,
}

/// Who this device is in its network.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Me {
    pub name: String,
    pub address: Ipv4Addr,
    /// May run the network from the app.
    pub owner: bool,
    /// When the access ends, Unix seconds; None is no term.
    pub expires_at: Option<i64>,
    pub exit: ExitChoice,
    pub group: Option<Group>,
}

/// An exit of the network.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Exit {
    pub index: u32,
    pub name: String,
    /// It works right now.
    pub up: bool,
    /// Percent of the users the shares give it.
    pub share: u32,
}

#[derive(Deserialize)]
struct Exits {
    exits: Vec<Exit>,
}

/// Where the user wants to be.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase", tag = "kind", content = "index")]
pub enum Destination {
    /// As the group or the shares decide.
    Auto,
    /// Kept on this exit.
    Exit(u32),
}

impl Destination {
    fn wire(self) -> serde_json::Value {
        match self {
            Self::Auto => serde_json::json!({ "exit": "auto" }),
            Self::Exit(index) => serde_json::json!({ "exit": index }),
        }
    }
}

#[derive(Deserialize)]
struct Refusal {
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    detail: Option<String>,
}

/// The app API of one network.
pub struct AppClient {
    http: reqwest::Client,
    base: Url,
}

impl AppClient {
    /// The API at the entry's tunnel address: the DNS server the device's config names.
    pub fn new(entry: Ipv4Addr) -> Result<Self, Error> {
        let base = Url::parse(&format!("http://{entry}:{API_PORT}/app/api/")).map_err(|_| Error::BadAnswer)?;
        Self::at(base)
    }

    /// The API at another address: for tests against a stand-in.
    pub fn at(base: Url) -> Result<Self, Error> {
        let http = reqwest::Client::builder()
            .timeout(TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy() // the request must leave through the tunnel, as the device
            .build()
            .map_err(|_| Error::OutsideTunnel)?;
        Ok(Self { http, base })
    }

    fn address(&self, path: &str) -> Result<Url, Error> {
        self.base.join(path).map_err(|_| Error::BadAnswer)
    }

    pub async fn me(&self) -> Result<Me, Error> {
        let answer = self.http.get(self.address("me")?).send().await.map_err(|_| Error::OutsideTunnel)?;
        read(answer).await
    }

    pub async fn exits(&self) -> Result<Vec<Exit>, Error> {
        let answer = self.http.get(self.address("exits")?).send().await.map_err(|_| Error::OutsideTunnel)?;
        let exits: Exits = read(answer).await?;
        Ok(exits.exits)
    }

    /// Keeps the user on an exit, or lets the group and the shares decide again.
    pub async fn choose(&self, destination: Destination) -> Result<(), Error> {
        let answer = self
            .http
            .post(self.address("exit")?)
            .header(APP_HEADER, APP_HEADER_VALUE)
            .timeout(CHANGE_TIMEOUT)
            .json(&destination.wire())
            .send()
            .await
            .map_err(|_| Error::OutsideTunnel)?;
        let _: serde_json::Value = read(answer).await?;
        Ok(())
    }
}

async fn read<T: for<'de> Deserialize<'de>>(answer: reqwest::Response) -> Result<T, Error> {
    let status = answer.status();
    if status == StatusCode::FORBIDDEN {
        return Err(Error::OutsideTunnel);
    }
    if !status.is_success() {
        let refusal: Refusal = answer.json().await.map_err(|_| Error::BadAnswer)?;
        return Err(refusal.error.or(refusal.detail).map(Error::Refused).unwrap_or(Error::BadAnswer));
    }
    answer.json().await.map_err(|_| Error::BadAnswer)
}
