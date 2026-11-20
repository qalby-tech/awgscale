//! The core against a stand-in that answers the way the panel does
//! (panel/routes/invites.py, panel/routes/app.py). Every key here is made up.

use std::net::Ipv4Addr;

use awgscale_client::api::{AppClient, Destination, Filter};
use awgscale_client::invite::InvitationClient;
use awgscale_client::join::JoinLink;
use awgscale_client::profile::{FileStore, ProfileStore};
use awgscale_client::Error;
use serde_json::json;
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
