//! The owner's part of the app API (panel/routes/app_owner.py): the network read and changed from a device of
//! its owner. The server asks whether this device's user owns the network on every request; here a refusal
//! of that is `NotOwner`, and a server older than these paths answers `Ok(None)`: the app then shows what
//! it showed before (the panel does it).

use std::collections::BTreeMap;

use reqwest::StatusCode;
use serde::{Deserialize, Serialize};

use crate::api::{AppClient, Refusal, APP_HEADER, APP_HEADER_VALUE, CHANGE_TIMEOUT};
use crate::error::Error;

/// Where rules lead is applied by the routing and the resolver: the server waits up to about five minutes.
const RULES_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(300);
/// The shares of the exits move people: the server waits up to three minutes.
const SHARES_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(200);

/// What goes abroad: only the listed sites, or everything foreign.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Mode {
    #[default]
    #[serde(rename = "list")]
    List,
    #[serde(rename = "geo")]
    Foreign,
}

/// Where a rule leads.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Way {
    Exit,
    Direct,
    Block,
    /// As if it were not there.
    Off,
}

/// An exit as its owner sees it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnerExit {
    pub index: u32,
    pub name: String,
    /// up, down or unknown.
    pub state: String,
    #[serde(default)]
    pub country: Option<String>,
    /// Its share of new users as stored: what a change of the shares is checked against.
    #[serde(default)]
    pub weight: u32,
    /// …in percent of all exits.
    #[serde(default)]
    pub share: u32,
    #[serde(default)]
    pub users: u32,
    #[serde(default)]
    pub online: u32,
}

/// A rule: a group of sites, where it leads, and for whom.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnerRule {
    pub id: String,
    pub name: String,
    pub route: Way,
    /// Where it leads when it is on: a rule switched off keeps it (never `Off`).
    pub way: Way,
    /// How many sites it names.
    #[serde(default)]
    pub items: u32,
    /// A list by link.
    #[serde(default)]
    pub by_link: bool,
    /// It may go directly or be blocked (sites only, written by hand).
    #[serde(default)]
    pub movable: bool,
    /// The groups of people it is for; empty: everybody.
    #[serde(default)]
    pub groups: Vec<String>,
}

/// A group of people; `id` None is the people in no group.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnerGroup {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub filter: Option<String>,
    #[serde(default)]
    pub space: bool,
    #[serde(default)]
    pub users: u32,
    #[serde(default)]
    pub online: u32,
}

/// The day's lookups by where they went.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Traffic {
    #[serde(default)]
    pub exit: u64,
    #[serde(default)]
    pub direct: u64,
    #[serde(default)]
    pub block: u64,
}

/// The network in one answer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Network {
    #[serde(default)]
    pub mode: Mode,
    #[serde(default)]
    pub single_server: bool,
    #[serde(default)]
    pub users: u32,
    #[serde(default)]
    pub online: u32,
    #[serde(default)]
    pub traffic: Traffic,
    #[serde(default)]
    pub exits: Vec<OwnerExit>,
    #[serde(default)]
    pub rules: Vec<OwnerRule>,
    /// The version of the rules the app shows: a change based on another one is refused.
    #[serde(default)]
    pub rules_v: String,
    #[serde(default)]
    pub groups: Vec<OwnerGroup>,
    /// The last look at the network is old.
    #[serde(default)]
    pub stale: bool,
}

/// One line of the change journal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Note {
    /// Unix seconds.
    pub t: f64,
    pub text: String,
    #[serde(default)]
    pub bad: bool,
    /// panel, bot, or none: the system itself.
    #[serde(default)]
    pub who: Option<String>,
}

#[derive(Deserialize)]
struct Journal {
    #[serde(default)]
    items: Vec<Note>,
}

/// A rule sent elsewhere.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuleChange {
    pub rule: String,
    pub route: Way,
}

/// What an invitation is for.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InvitationWish {
    /// The group the invited people join; None: no group.
    #[serde(default)]
    pub group: Option<String>,
    /// How many devices may use it, 1–10.
    pub uses: u32,
    /// How many days it works, 1–30.
    pub days: u32,
}

#[derive(Deserialize)]
struct Made {
    token: String,
}

#[derive(Deserialize)]
struct Code {
    png: String,
}

/// A new invitation: its link and the link's QR code (a PNG in base64).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Invitation {
    pub link: String,
    pub png: String,
}

/// The link an invitation's secret makes at the network's domain: what the panel shows, and what another
/// phone's camera or the app's own join screen takes.
pub fn invitation_link(host: &str, token: &str) -> String {
    format!("https://{host}/i/{token}")
}

impl AppClient {
    /// The network: exits, rules, groups of people. None: the server has no owner's part yet.
    pub async fn network(&self) -> Result<Option<Network>, Error> {
        let answer = self.http.get(self.address("owner/network")?).send().await.map_err(|_| Error::OutsideTunnel)?;
        owned(answer).await
    }

    /// The change journal, the latest first. None: the server has no owner's part yet.
    pub async fn journal(&self) -> Result<Option<Vec<Note>>, Error> {
        let answer = self.http.get(self.address("owner/journal")?).send().await.map_err(|_| Error::OutsideTunnel)?;
        let journal: Option<Journal> = owned(answer).await?;
        Ok(journal.map(|journal| journal.items))
    }

    /// Rules sent elsewhere, all together, on top of the version of the list the app showed.
    pub async fn route_rules(&self, changes: &[RuleChange], base: &str) -> Result<(), Error> {
        let body = serde_json::json!({ "changes": changes, "base": base });
        self.change("owner/rules", &body, RULES_TIMEOUT).await
    }

    /// The shares of the exits in percent, by their number, on top of the weights the app showed.
    pub async fn set_shares(&self, shares: &BTreeMap<u32, u32>, base: &BTreeMap<u32, u32>) -> Result<(), Error> {
        let keyed = |map: &BTreeMap<u32, u32>| map.iter().map(|(index, value)| (index.to_string(), *value)).collect::<BTreeMap<_, _>>();
        let body = serde_json::json!({ "weights": keyed(shares), "base": keyed(base) });
        self.change("owner/weights", &body, SHARES_TIMEOUT).await
    }

    /// A new invitation into the network whose domain is `host`, with its QR code.
    pub async fn invite(&self, wish: &InvitationWish, host: &str) -> Result<Invitation, Error> {
        let made: Made = self.posted("owner/invite", &serde_json::to_value(wish).map_err(|_| Error::BadAnswer)?, CHANGE_TIMEOUT).await?;
        let link = invitation_link(host, &made.token);
        let code: Code = self.posted("owner/invite/qr", &serde_json::json!({ "link": link }), CHANGE_TIMEOUT).await?;
        Ok(Invitation { link, png: code.png })
    }

    async fn change(&self, path: &str, body: &serde_json::Value, timeout: std::time::Duration) -> Result<(), Error> {
        let _: serde_json::Value = self.posted(path, body, timeout).await?;
        Ok(())
    }

    async fn posted<T: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        body: &serde_json::Value,
        timeout: std::time::Duration,
    ) -> Result<T, Error> {
        let answer = self
            .http
            .post(self.address(path)?)
            .header(APP_HEADER, APP_HEADER_VALUE)
            .timeout(timeout)
            .json(body)
            .send()
            .await
            .map_err(|_| Error::OutsideTunnel)?;
        owned(answer).await?.ok_or(Error::NoOwnerPart)
    }
}

/// An answer of the owner's part: refused 403 is not an owner (the paths are inside the tunnel already, the
/// server checked that first and says so in its own words), 404 is a server without these paths.
async fn owned<T: for<'de> Deserialize<'de>>(answer: reqwest::Response) -> Result<Option<T>, Error> {
    let status = answer.status();
    if status == StatusCode::NOT_FOUND {
        return Ok(None);
    }
    if status == StatusCode::FORBIDDEN {
        let refusal: Refusal = answer.json().await.map_err(|_| Error::NotOwner)?;
        return Err(match refusal.error.as_deref() {
            Some(OUTSIDE) => Error::OutsideTunnel,
            _ => Error::NotOwner,
        });
    }
    if !status.is_success() {
        let refusal: Refusal = answer.json().await.map_err(|_| Error::BadAnswer)?;
        return Err(refusal.error.or(refusal.detail).map(Error::Refused).unwrap_or(Error::BadAnswer));
    }
    answer.json().await.map(Some).map_err(|_| Error::BadAnswer)
}

/// The server's words for a request from outside the tunnel (panel/routes/app.py).
const OUTSIDE: &str = "только через VPN";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_network_as_the_server_sends_it() {
        let answer = r#"{"ok":true,"mode":"geo","single_server":false,"users":23,"online":7,
            "traffic":{"exit":620,"direct":310,"block":70},
            "exits":[{"index":1,"name":"exit1-nl","state":"up","country":"nl","weight":3,"share":75,"users":17,"online":4}],
            "rules":[{"id":"abc123","name":"YouTube","route":"exit","way":"exit","items":10,"by_link":false,"movable":true,"groups":[]},
                     {"id":"def456","name":"HaGeZi","route":"off","way":"block","items":0,"by_link":true,"movable":false,"groups":["Дети"]}],
            "rules_v":"0123456789ab","groups":[{"id":null,"name":"","filter":null,"space":false,"users":2,"online":0}],"stale":false}"#;
        let network: Network = serde_json::from_str(answer).unwrap();
        assert_eq!(network.mode, Mode::Foreign);
        assert_eq!(network.traffic, Traffic { exit: 620, direct: 310, block: 70 });
        assert_eq!(network.rules[1].route, Way::Off);
        assert_eq!(network.rules[1].groups, vec!["Дети".to_owned()]);
        assert_eq!((network.exits[0].weight, network.exits[0].share), (3, 75));
        assert_eq!(network.groups[0].id, None);
    }

    #[test]
    fn a_later_server_may_say_more_and_an_earlier_less() {
        let more = r#"{"ok":true,"mode":"list","exits":[],"rules":[],"groups":[],"something":"new"}"#;
        assert_eq!(serde_json::from_str::<Network>(more).unwrap(), Network::default());
        let note: Note = serde_json::from_str(r#"{"t":1764000000,"text":"Создан пользователь ann"}"#).unwrap();
        assert_eq!((note.bad, note.who), (false, None));
    }

    #[test]
    fn changes_as_the_server_takes_them() {
        let change = RuleChange { rule: "abc123".into(), route: Way::Direct };
        assert_eq!(serde_json::to_value(&change).unwrap(), serde_json::json!({"rule": "abc123", "route": "direct"}));
        let wish: InvitationWish = serde_json::from_str(r#"{"uses":1,"days":7}"#).unwrap();
        assert_eq!(serde_json::to_value(&wish).unwrap(), serde_json::json!({"group": null, "uses": 1, "days": 7}));
        assert_eq!(invitation_link("vpn.example.org", "AAAA"), "https://vpn.example.org/i/AAAA");
    }
}
