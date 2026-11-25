//! The core against a stand-in that answers the way the panel does
//! (panel/routes/invites.py, panel/routes/app.py). Every key here is made up.

use std::net::Ipv4Addr;

use awgscale_client::api::{AppClient, Destination, Filter};
use awgscale_client::invite::InvitationClient;
use awgscale_client::join::JoinLink;
use awgscale_client::owner::{base_of, InvitationWish, Mode, RuleChange, Way};
use awgscale_client::profile::{FileStore, ProfileStore};
use awgscale_client::Error;
use serde_json::json;
use std::collections::BTreeMap;
use url::Url;
use wiremock::matchers::{body_json, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const CODE: &str = "Zx3kQ0aB1cD2eF3gH4iJ5k";
const CONF: &str = "[Interface]\nPrivateKey = made-up\nAddress = 172.16.17.15/32\nDNS = 172.16.17.1\n\n[Peer]\nPublicKey = made-up\n";

async fn invitation(server: &MockServer) -> InvitationClient {
    InvitationClient::at(Url::parse(&server.uri()).unwrap(), CODE).unwrap()
}

#[tokio::test]
async fn joins_and_keeps_the_profile() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path(format!("/i/{CODE}/info")))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({"ok": true, "group": null, "left": 1, "total": 1, "expires_at": 1_900_000_000})),
        )
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(format!("/i/{CODE}")))
        .and(header("X-Invite", "1"))
        .and(body_json(json!({"name": "phone"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "ok": true, "name": "phone", "uri": "vpn://made-up", "conf": CONF, "left": 0, "group": "Дом", "owner": true
        })))
        .expect(1)
        .mount(&server)
        .await;

    let client = invitation(&server).await;
    let info = awgscale_client::look(&client).await.unwrap();
    assert_eq!((info.left, info.total, info.group), (1, 1, None));

    let directory = tempfile::tempdir().unwrap();
    let store = FileStore::new(directory.path().to_path_buf());
    let link = JoinLink::parse(&format!("awgscale://join?h=vpn.example.org&c={CODE}")).unwrap();
    let profile = awgscale_client::join(&client, &link, "phone", &store).await.unwrap();
    assert_eq!(profile.host, "vpn.example.org");
    assert_eq!(profile.entry, Ipv4Addr::new(172, 16, 17, 1));
    assert!(profile.owner);
    assert_eq!(store.load().unwrap(), Some(profile));
}

#[tokio::test]
async fn shows_why_a_link_does_not_work() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path(format!("/i/{CODE}/info")))
        .respond_with(
            ResponseTemplate::new(404).set_body_json(json!({"ok": false, "error": "Ссылка недействительна или уже использована"})),
        )
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(format!("/i/{CODE}")))
        .respond_with(ResponseTemplate::new(400).set_body_json(json!({"ok": false, "error": "Имя уже занято"})))
        .mount(&server)
        .await;
    let client = invitation(&server).await;
    assert!(matches!(client.info().await, Err(Error::InvitationGone)));
    assert!(matches!(client.redeem("phone").await, Err(Error::Refused(text)) if text == "Имя уже занято"));
    assert!(matches!(client.redeem("bad name").await, Err(Error::Refused(_))));
}

#[tokio::test]
async fn reads_me_and_the_exits() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/app/api/me"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "ok": true, "name": "phone", "address": "172.16.17.15", "owner": false, "expires_at": null,
            "exit": {"pinned": 2, "group": null, "name": "nl-1"},
            "group": {"name": "Дача", "filter": "kids", "space": true, "members": [{"name": "tv", "address": "172.16.17.9"}]}
        })))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/app/api/exits"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true, "exits": [
            {"index": 1, "name": "de-1", "up": true, "share": 67}, {"index": 2, "name": "nl-1", "up": false, "share": 33}
        ]})))
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    let me = api.me().await.unwrap();
    assert_eq!(me.exit.pinned, Some(2));
    let group = me.group.unwrap();
    assert_eq!(group.filter, Filter::Kids);
    assert_eq!(group.members[0].address, Ipv4Addr::new(172, 16, 17, 9));
    assert_eq!(me.filter, None, "a server older than the choice sends none");
    let exits = api.exits().await.unwrap();
    assert_eq!(exits.iter().map(|exit| (exit.index, exit.up, exit.share)).collect::<Vec<_>>(), [(1, true, 67), (2, false, 33)]);
}

#[tokio::test]
async fn changes_the_exit_with_the_app_header() {
    let server = MockServer::start().await;
    for (body, answer) in [(json!({"exit": 2}), 200), (json!({"exit": "auto"}), 200)] {
        Mock::given(method("POST"))
            .and(path("/app/api/exit"))
            .and(header("X-Awgscale", "1"))
            .and(body_json(body))
            .respond_with(ResponseTemplate::new(answer).set_body_json(json!({"ok": true})))
            .expect(1)
            .mount(&server)
            .await;
    }
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    api.choose(Destination::Exit(2)).await.unwrap();
    api.choose(Destination::Auto).await.unwrap();
}

#[tokio::test]
async fn outside_the_tunnel_says_so() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/app/api/me"))
        .respond_with(ResponseTemplate::new(403).set_body_json(json!({"detail": "только через VPN"})))
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    assert!(matches!(api.me().await, Err(Error::OutsideTunnel)));
    let nobody = AppClient::at(Url::parse("http://127.0.0.1:9/app/api/").unwrap()).unwrap();
    assert!(matches!(nobody.me().await, Err(Error::OutsideTunnel)));
}

#[tokio::test]
async fn does_not_spend_the_link_when_it_cannot_keep_the_result() {
    let server = MockServer::start().await;
    Mock::given(method("POST")).and(path(format!("/i/{CODE}"))).respond_with(ResponseTemplate::new(200)).expect(0).mount(&server).await;
    let directory = tempfile::tempdir().unwrap();
    let blocker = directory.path().join("file");
    std::fs::write(&blocker, b"").unwrap();
    let store = FileStore::new(blocker.join("data")); // a directory under a file: nothing can be written
    let link = JoinLink::parse(&format!("awgscale://join?h=vpn.example.org&c={CODE}")).unwrap();
    let client = invitation(&server).await;
    assert!(matches!(awgscale_client::join(&client, &link, "phone", &store).await, Err(Error::Storage)));
}

#[tokio::test]
async fn the_owner_reads_the_network_and_an_older_server_has_none() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/app/api/owner/network"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "ok": true, "mode": "list", "users": 3, "online": 1, "traffic": {"exit": 5, "direct": 3, "block": 1},
            "exits": [{"index": 1, "name": "nl-1", "state": "up", "country": "nl", "weight": 1, "share": 100, "users": 3, "online": 1}],
            "rules": [{"id": "abc123", "name": "YouTube", "route": "exit", "way": "exit", "items": 10, "by_link": false, "movable": true, "groups": []}],
            "rules_v": "0123456789ab", "groups": []
        })))
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    let network = api.network().await.unwrap().unwrap();
    assert_eq!((network.users, network.rules[0].route, network.rules_v.as_str()), (3, Way::Exit, "0123456789ab"));
    // no journal path on this server: an older one
    assert_eq!(api.journal().await.unwrap(), None);
}

#[tokio::test]
async fn not_an_owner_is_told_so_and_outside_the_tunnel_too() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/app/api/owner/network"))
        .respond_with(ResponseTemplate::new(403).set_body_json(json!({"ok": false, "error": "только для владельца сети"})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/app/api/owner/journal"))
        .respond_with(ResponseTemplate::new(403).set_body_json(json!({"ok": false, "error": "только через VPN"})))
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    assert!(matches!(api.network().await, Err(Error::NotOwner)));
    assert!(matches!(api.journal().await, Err(Error::OutsideTunnel)));
}

#[tokio::test]
async fn the_owner_changes_rules_shares_and_invites_with_the_app_header() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/rules"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"changes": [{"rule": "abc123", "route": "block"}], "base": "0123456789ab"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/weights"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"weights": {"1": 60, "2": 40}, "base": {"1": 1, "2": 1}})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/invite"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"group": null, "uses": 2, "days": 7})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true, "id": "abc123", "token": "T"})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/invite/qr"))
        .and(body_json(json!({"link": "https://vpn.example.org/i/T"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true, "png": "iVBOR"})))
        .expect(1)
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    api.route_rules(&[RuleChange { rule: "abc123".into(), route: Some(Way::Block), groups: None }], None, "0123456789ab").await.unwrap();
    api.set_shares(&BTreeMap::from([(1, 60), (2, 40)]), &BTreeMap::from([(1, 1), (2, 1)])).await.unwrap();
    let invitation = api.invite(&InvitationWish { group: None, uses: 2, days: 7 }, &base_of("vpn.example.org")).await.unwrap();
    assert_eq!((invitation.link.as_str(), invitation.png.as_str()), ("https://vpn.example.org/i/T", "iVBOR"));
}

#[tokio::test]
async fn the_owner_orders_and_scopes_rules_sets_the_mode_and_runs_people() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/rules"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"changes": [{"rule": "def456", "groups": ["grp001"]}], "order": ["def456", "abc123"], "base": "v"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/mode"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"mode": "geo"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/app/api/owner/people"))
        .and(wiremock::matchers::query_param("group", "grp001"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "ok": true, "total": 1, "people": [{"name": "ann", "online": true, "paused": false, "seen": 1764000000, "owner": false, "me": false}]
        })))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/people/pause"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"names": ["ann"], "on": true})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/app/api/owner/people/remove"))
        .and(header("X-Awgscale", "1"))
        .and(body_json(json!({"names": ["phone"]})))
        .respond_with(
            ResponseTemplate::new(400).set_body_json(json!({"ok": false, "error": "это устройство, с которого вы управляете сетью"})),
        )
        .expect(1)
        .mount(&server)
        .await;
    let api = AppClient::at(Url::parse(&format!("{}/app/api/", server.uri())).unwrap()).unwrap();
    let order = vec!["def456".to_owned(), "abc123".to_owned()];
    api.route_rules(&[RuleChange { rule: "def456".into(), route: None, groups: Some(vec!["grp001".into()]) }], Some(&order), "v")
        .await
        .unwrap();
    api.set_mode(Mode::Foreign).await.unwrap();
    let people = api.people(Some("grp001")).await.unwrap().unwrap();
    assert_eq!((people.total, people.people[0].name.as_str(), people.people[0].online), (1, "ann", true));
    api.pause(&["ann".to_owned()], true).await.unwrap();
    match api.remove(&["phone".to_owned()]).await {
        Err(Error::Refused(said)) => assert!(said.contains("управляете")),
        other => panic!("the server's refusal in its own words, not {other:?}"),
    }
}
