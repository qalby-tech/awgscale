//! The tunnel of a computer: the service `qalby daemon` (app/qalby) holds it, as
//! root; the app asks it over a local socket that only root and the group
//! `awgscale` may use (app/qalby/control). One JSON request per connection.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::secret::Secret;
use crate::tunnel::{Tunnel, TunnelReport, TunnelState};

/// Where the service listens (SocketPath in app/qalby/cmd/qalby).
pub const SOCKET_PATH: &str = "/run/awgscale/control.sock";
/// Bringing the tunnel up waits for the server's first answer (20 s in the service).
const TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Serialize)]
#[serde(rename_all = "lowercase")]
enum Command {
    Up,
    Down,
    Status,
}

#[derive(Serialize)]
struct Request<'a> {
    command: Command,
    #[serde(skip_serializing_if = "Option::is_none")]
    conf: Option<&'a str>,
}

/// Whether the network's DNS answers the computer's names (DNSState in app/qalby/tunnel).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
enum DnsState {
    Set,
    Unmanaged,
    Failed,
    None,
}

#[derive(Deserialize)]
struct Status {
    state: TunnelState,
    #[serde(default)]
    dns: Option<DnsState>,
}

impl Status {
    fn report(&self) -> TunnelReport {
        let warning = match (self.state, self.dns) {
            (TunnelState::Down, _) => None,
            (_, Some(DnsState::Unmanaged)) => Some("DNS сети не подключён: на компьютере нет systemd-resolved".to_owned()),
            (_, Some(DnsState::Failed)) => Some("DNS сети не подключился: systemd-resolved отказал".to_owned()),
            _ => None,
        };
        TunnelReport { state: self.state, warning }
    }
}

#[derive(Deserialize)]
struct Answer {
    ok: bool,
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    status: Option<Status>,
}

/// The service's tunnel.
pub struct ServiceTunnel {
    socket: PathBuf,
}

impl ServiceTunnel {
    pub fn new(socket: PathBuf) -> Self {
        Self { socket }
    }

    /// The service at its usual place. Asked anew each time: it may be installed or started later.
    pub fn usual() -> Self {
        Self::new(PathBuf::from(SOCKET_PATH))
    }

    #[cfg(unix)]
    fn ask(&self, request: &Request<'_>) -> Result<TunnelReport, Error> {
        use std::os::unix::net::UnixStream;
        let refused = |error: std::io::Error| match error.kind() {
            std::io::ErrorKind::PermissionDenied => {
                Error::Tunnel("нет прав управлять VPN — добавьте себя в группу awgscale и войдите заново".to_owned())
            }
            _ => Error::Tunnel("служба awgscale не запущена".to_owned()),
        };
        let mut stream = UnixStream::connect(&self.socket).map_err(refused)?;
        stream.set_read_timeout(Some(TIMEOUT)).map_err(refused)?;
        stream.set_write_timeout(Some(TIMEOUT)).map_err(refused)?;
        let mut line = serde_json::to_vec(request).map_err(|_| Error::Tunnel("сбой".to_owned()))?;
        line.push(b'\n');
        stream.write_all(&line).map_err(refused)?;
        line.fill(0); // it held the private key
        let mut reply = String::new();
        BufReader::new(stream).read_line(&mut reply).map_err(|_| Error::Tunnel("служба awgscale не ответила".to_owned()))?;
        let answer: Answer = serde_json::from_str(&reply).map_err(|_| Error::Tunnel("служба awgscale ответила непонятно".to_owned()))?;
        if !answer.ok {
            return Err(Error::Tunnel(answer.error.unwrap_or_else(|| "сбой".to_owned())));
        }
        Ok(answer.status.map(|status| status.report()).unwrap_or(TunnelReport { state: TunnelState::Down, warning: None }))
    }

    #[cfg(not(unix))]
    fn ask(&self, _request: &Request<'_>) -> Result<TunnelReport, Error> {
        Err(Error::Tunnel("на этой системе служба VPN ещё не сделана".to_owned()))
    }
}

impl Tunnel for ServiceTunnel {
    fn up(&self, conf: &Secret) -> Result<(), Error> {
        self.ask(&Request { command: Command::Up, conf: Some(conf.expose()) }).map(|_| ())
    }

    fn down(&self) -> Result<(), Error> {
        self.ask(&Request { command: Command::Down, conf: None }).map(|_| ())
    }

    fn state(&self) -> TunnelState {
        self.report().state
    }

    fn report(&self) -> TunnelReport {
        self.ask(&Request { command: Command::Status, conf: None }).unwrap_or(TunnelReport { state: TunnelState::Down, warning: None })
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;

    /// A stand-in for the service: answers one request the way control.go does, and says what it got.
    fn service(answer: &'static str) -> (tempfile::TempDir, ServiceTunnel, std::thread::JoinHandle<String>) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("control.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let handle = std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut request = String::new();
            BufReader::new(&stream).read_line(&mut request).unwrap();
            (&stream).write_all(answer.as_bytes()).unwrap();
            request
        });
        (directory, ServiceTunnel::new(path), handle)
    }

    #[test]
    fn brings_the_tunnel_up_with_the_config() {
        let (_directory, tunnel, handle) = service("{\"ok\":true,\"status\":{\"state\":\"up\",\"handshake\":1}}\n");
        tunnel.up(&Secret::new("[Interface]\n".to_owned())).unwrap();
        let request: serde_json::Value = serde_json::from_str(&handle.join().unwrap()).unwrap();
        assert_eq!(request, serde_json::json!({"command": "up", "conf": "[Interface]\n"}));
    }

    #[test]
    fn shows_the_services_refusal() {
        let (_directory, tunnel, _handle) = service("{\"ok\":false,\"error\":\"сервер не ответил\"}\n");
        let error = tunnel.up(&Secret::new(String::new())).unwrap_err();
        assert_eq!(error.to_string(), "Не удалось включить VPN: сервер не ответил");
    }

    #[test]
    fn reads_the_state() {
        let (_directory, tunnel, _handle) = service("{\"ok\":true,\"status\":{\"state\":\"connecting\"}}\n");
        assert_eq!(tunnel.state(), TunnelState::Connecting);
    }

    #[test]
    fn says_when_the_networks_dns_is_not_used() {
        let (_directory, tunnel, _handle) = service("{\"ok\":true,\"status\":{\"state\":\"up\",\"dns\":\"unmanaged\"}}\n");
        let report = tunnel.report();
        assert_eq!(report.state, TunnelState::Up);
        assert!(report.warning.unwrap().contains("systemd-resolved"));
    }

    #[test]
    fn no_service_is_down() {
        let tunnel = ServiceTunnel::new(PathBuf::from("/nonexistent/control.sock"));
        assert_eq!(tunnel.state(), TunnelState::Down);
        assert!(tunnel.up(&Secret::new(String::new())).unwrap_err().to_string().contains("не запущена"));
    }
}
