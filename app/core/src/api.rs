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
/// Whether a connection given by hand has the app API behind it is asked in the background, briefly.
const PROBE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);
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

/// How the user's requests are filtered, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FilterChoice {
    /// The filtering that applies.
    pub applies: Filter,
    /// What the user chose themselves; None is as their group.
    pub own: Option<Filter>,
    /// Their group is a children's one: it is not changed from the app.
    pub locked: bool,
}

/// What the user may choose for their own filtering.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase", tag = "kind", content = "filter")]
pub enum FilterWish {
    /// As their group.
    Auto,
    /// This level.
    Level(Filter),
}

impl FilterWish {
    fn wire(self) -> serde_json::Value {
        match self {
            Self::Auto => serde_json::json!({ "filter": "auto" }),
            Self::Level(level) => serde_json::json!({ "filter": level }),
        }
    }
}

/// Where the user's traffic goes: as the network's mode says, or all of it through their exit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Route {
    /// As the network decides: some sites from the server, the rest abroad.
    Smart,
    /// Everything through the exit, Russian sites too.
    All,
}

/// The user's route, and whether there is anything to choose.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RouteChoice {
    /// Everything through the exit.
    pub all: bool,
    /// The network has an exit: a single server has none.
    pub available: bool,
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
    /// Absent from a server older than the choice of one's own filtering.
    #[serde(default)]
    pub filter: Option<FilterChoice>,
    /// Absent from a server older than the choice of one's own route.
    #[serde(default)]
    pub route: Option<RouteChoice>,
}

/// An exit of the network. A server older than the choice of a location by country and address
/// sends only the index, the name, whether it works and its share: the rest are then empty.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Exit {
    pub index: u32,
    pub name: String,
    /// It works right now.
    pub up: bool,
    /// Percent of the users the shares give it.
    pub share: u32,
    /// Its public IPv4: what a person abroad is seen as. Empty from an older server.
    #[serde(default)]
    pub address: String,
    /// ISO 3166-1 alpha-2, lower case; None when unknown (or from an older server).
    #[serde(default)]
    pub country: Option<String>,
    /// The people kept on it now.
    #[serde(default)]
    pub users: u32,
    /// Of them, the ones online now.
    #[serde(default)]
    pub online: u32,
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
        Self::at(base_at(entry)?)
    }

    /// The API at the base given: an invited network's (`base_at`), a connection given by hand's once found
    /// there, a stand-in's in the tests.
    pub fn at(base: Url) -> Result<Self, Error> {
        Self::waiting(base, TIMEOUT)
    }

    /// The API asked only whether it is there: a short wait, the person is not kept waiting for it.
    pub fn probing(base: Url) -> Result<Self, Error> {
        Self::waiting(base, PROBE_TIMEOUT)
    }

    fn waiting(base: Url, timeout: std::time::Duration) -> Result<Self, Error> {
        let http = reqwest::Client::builder()
            .timeout(timeout)
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

    /// The user's own filtering, or their group's again.
    pub async fn choose_filter(&self, wish: FilterWish) -> Result<(), Error> {
        let answer = self
            .http
            .post(self.address("filter")?)
            .header(APP_HEADER, APP_HEADER_VALUE)
            .timeout(CHANGE_TIMEOUT)
            .json(&wish.wire())
            .send()
            .await
            .map_err(|_| Error::OutsideTunnel)?;
        let _: serde_json::Value = read(answer).await?;
        Ok(())
    }

    /// All of the user's traffic through their exit, or as the network's mode says again.
    pub async fn choose_route(&self, route: Route) -> Result<(), Error> {
        let answer = self
            .http
            .post(self.address("route")?)
            .header(APP_HEADER, APP_HEADER_VALUE)
            .timeout(CHANGE_TIMEOUT)
            .json(&serde_json::json!({ "route": route }))
            .send()
            .await
            .map_err(|_| Error::OutsideTunnel)?;
        let _: serde_json::Value = read(answer).await?;
        Ok(())
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

/// Where the app API of a network answers: at the entry's tunnel address (Traefik serves it there only, on
/// its own entrypoint, stack/traefik), `http://172.16.17.1:8088/app/api/`.
pub fn base_at(entry: Ipv4Addr) -> Result<Url, Error> {
    Url::parse(&format!("http://{entry}:{API_PORT}/app/api/")).map_err(|_| Error::BadAnswer)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filter_wishes_as_the_interface_sends_them() {
        let level: FilterWish = serde_json::from_str(r#"{"kind":"level","filter":"proplus"}"#).unwrap();
        let auto: FilterWish = serde_json::from_str(r#"{"kind":"auto"}"#).unwrap();
        assert_eq!(level.wire(), serde_json::json!({"filter": "proplus"}));
        assert_eq!(auto.wire(), serde_json::json!({"filter": "auto"}));
    }

    #[test]
    fn routes_and_an_older_server() {
        assert_eq!(serde_json::json!({ "route": Route::All }), serde_json::json!({"route": "all"}));
        assert_eq!(serde_json::from_str::<Route>(r#""smart""#).unwrap(), Route::Smart);
        let older = r#"{"name":"a","address":"172.16.17.2","owner":false,"expires_at":null,"exit":{"pinned":null,"group":null,"name":null},"group":null}"#;
        let me: Me = serde_json::from_str(older).unwrap();
        assert_eq!((me.filter, me.route), (None, None));
    }

    #[test]
    fn exits_with_their_address_and_country() {
        let answer = r#"{"ok":true,"exits":[
            {"index":1,"name":"exit1-nl","address":"198.51.100.38","country":"nl","up":true,"share":67,"users":17,"online":4},
            {"index":2,"name":"exit2","address":"203.0.113.7","country":null,"up":false,"share":0,"users":0,"online":0}]}"#;
        let exits: Exits = serde_json::from_str(answer).unwrap();
        let first = Exit {
            index: 1,
            name: "exit1-nl".into(),
            up: true,
            share: 67,
            address: "198.51.100.38".into(),
            country: Some("nl".into()),
            users: 17,
            online: 4,
        };
        assert_eq!(exits.exits[0], first);
        assert_eq!((exits.exits[1].country.as_deref(), exits.exits[1].up), (None, false));
        assert_eq!(exits.exits[1].address, "203.0.113.7");
    }

    #[test]
    fn exits_from_an_older_server() {
        let older = r#"{"ok":true,"exits":[{"index":3,"name":"exit3","up":true,"share":33}]}"#;
        let exits: Exits = serde_json::from_str(older).unwrap();
        let exit = &exits.exits[0];
        assert_eq!((exit.index, exit.name.as_str(), exit.up, exit.share), (3, "exit3", true, 33));
        assert_eq!((exit.address.as_str(), exit.country.as_deref(), exit.users, exit.online), ("", None, 0, 0));
        // the interface gets the new fields filled either way
        let shown = serde_json::to_value(exit).unwrap();
        assert_eq!(shown["address"], "");
        assert_eq!(shown["country"], serde_json::Value::Null);
        assert_eq!((shown["users"].as_u64(), shown["online"].as_u64()), (Some(0), Some(0)));
    }

    #[test]
    fn destinations_as_the_interface_sends_them() {
        let exit: Destination = serde_json::from_str(r#"{"kind":"exit","index":2}"#).unwrap();
        let auto: Destination = serde_json::from_str(r#"{"kind":"auto"}"#).unwrap();
        assert_eq!(exit, Destination::Exit(2));
        assert_eq!(auto, Destination::Auto);
        assert_eq!(exit.wire(), serde_json::json!({"exit": 2}));
        assert_eq!(auto.wire(), serde_json::json!({"exit": "auto"}));
    }
}
