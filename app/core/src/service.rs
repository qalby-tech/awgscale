//! The tunnel of a computer: the service `qalby daemon` (app/qalby) holds it, as
//! root or SYSTEM; the app asks it over a local channel (app/qalby/control): on
//! Linux a socket only root and the group `awgscale` may use, on Windows a named
//! pipe of SYSTEM. One JSON request per connection.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::secret::Secret;
use crate::tunnel::{Tunnel, TunnelOptions, TunnelReport, TunnelState};

/// Where the service listens (ChannelPath in app/qalby/cmd/qalby).
#[cfg(not(windows))]
pub const CHANNEL_PATH: &str = "/run/awgscale/control.sock";
#[cfg(windows)]
pub const CHANNEL_PATH: &str = r"\\.\pipe\awgscale";
/// Bringing the tunnel up waits for the server's first answer (20 s in the service). On Windows the
/// pipe has no timeout of its own: the service keeps to its own 60 s there.
#[cfg(unix)]
const TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

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
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    kill_switch: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    local_around: bool,
    #[serde(skip_serializing_if = "<[String]>::is_empty")]
    around: &'a [String],
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
        Self::new(PathBuf::from(CHANNEL_PATH))
    }

    fn ask(&self, request: &Request<'_>) -> Result<TunnelReport, Error> {
        let mut line = serde_json::to_vec(request).map_err(|_| Error::Tunnel("сбой".to_owned()))?;
        line.push(b'\n');
        let reply = channel::exchange(&self.socket, &line);
        line.fill(0); // it held the private key
        let answer: Answer = serde_json::from_str(&reply?).map_err(|_| Error::Tunnel("служба awgscale ответила непонятно".to_owned()))?;
        if !answer.ok {
            return Err(Error::Tunnel(answer.error.unwrap_or_else(|| "сбой".to_owned())));
        }
        Ok(answer.status.map(|status| status.report()).unwrap_or(TunnelReport { state: TunnelState::Down, warning: None }))
    }
}

fn refused(error: std::io::Error) -> Error {
    match error.kind() {
        std::io::ErrorKind::PermissionDenied => Error::Tunnel(REFUSED.to_owned()),
        _ => Error::Tunnel("служба awgscale не запущена".to_owned()),
    }
}

#[cfg(not(windows))]
const REFUSED: &str = "нет прав управлять VPN — добавьте себя в группу awgscale и войдите заново";
#[cfg(windows)]
const REFUSED: &str = "нет прав управлять VPN";

/// One request and its answer over the service's channel.
#[cfg(unix)]
mod channel {
    use super::*;
    use std::os::unix::net::UnixStream;

    pub fn exchange(path: &std::path::Path, request: &[u8]) -> Result<String, Error> {
        let mut stream = UnixStream::connect(path).map_err(refused)?;
        stream.set_read_timeout(Some(TIMEOUT)).map_err(refused)?;
        stream.set_write_timeout(Some(TIMEOUT)).map_err(refused)?;
        stream.write_all(request).map_err(refused)?;
        let mut reply = String::new();
        BufReader::new(stream).read_line(&mut reply).map_err(|_| Error::Tunnel("служба awgscale не ответила".to_owned()))?;
        Ok(reply)
    }
}

/// On Windows the channel is a named pipe; it is spoken to only when SYSTEM owns it, so that a
/// pipe somebody made under the service's name is never handed the config.
#[cfg(windows)]
mod channel {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::{LocalFree, ERROR_SUCCESS, HANDLE};
    use windows_sys::Win32::Security::Authorization::{GetSecurityInfo, SE_FILE_OBJECT};
    use windows_sys::Win32::Security::{
        CreateWellKnownSid, EqualSid, WinLocalSystemSid, OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, PSID,
    };

    const SID_MAX_SIZE: usize = 68; // SECURITY_MAX_SID_SIZE
    /// FILE_GENERIC_READ | FILE_WRITE_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES
    const PIPE_ACCESS: u32 = 0x0012_019b;
    /// SECURITY_IDENTIFICATION (SecurityIdentification << 16)
    const SECURITY_IDENTIFICATION: u32 = 0x0001_0000;

    pub fn exchange(path: &std::path::Path, request: &[u8]) -> Result<String, Error> {
        use std::os::windows::fs::OpenOptionsExt;
        // the rights the pipe gives the people logged on (pipeSecurity in app/qalby/control), no
        // more; and the service may learn who asks, never act as them
        let mut pipe =
            std::fs::OpenOptions::new().access_mode(PIPE_ACCESS).security_qos_flags(SECURITY_IDENTIFICATION).open(path).map_err(refused)?;
        if !owned_by_system(pipe.as_raw_handle() as HANDLE) {
            return Err(Error::Tunnel("канал службы awgscale принадлежит не ей — VPN не включён".to_owned()));
        }
        pipe.write_all(request).map_err(refused)?;
        let mut reply = String::new();
        BufReader::new(pipe).read_line(&mut reply).map_err(|_| Error::Tunnel("служба awgscale не ответила".to_owned()))?;
        Ok(reply)
    }

    fn owned_by_system(handle: HANDLE) -> bool {
        let mut owner: PSID = std::ptr::null_mut();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let mut system = [0u8; SID_MAX_SIZE];
        let mut size = SID_MAX_SIZE as u32;
        // SAFETY: the out-pointers are valid for the calls; the descriptor is freed once, below.
        unsafe {
            let status = GetSecurityInfo(
                handle,
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION,
                &mut owner,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut descriptor,
            );
            if status != ERROR_SUCCESS {
                return false;
            }
            let made = CreateWellKnownSid(WinLocalSystemSid, std::ptr::null_mut(), system.as_mut_ptr() as PSID, &mut size);
            let same = made != 0 && EqualSid(owner, system.as_mut_ptr() as PSID) != 0;
            LocalFree(descriptor as _);
            same
        }
    }
}

impl Tunnel for ServiceTunnel {
    fn up(&self, conf: &Secret, options: TunnelOptions) -> Result<(), Error> {
        self.ask(&Request {
            command: Command::Up,
            conf: Some(conf.expose()),
            kill_switch: options.kill_switch,
            local_around: options.local_around,
            around: &options.around,
        })
        .map(|_| ())
    }

    fn down(&self) -> Result<(), Error> {
        self.ask(&Request { command: Command::Down, conf: None, kill_switch: false, local_around: false, around: &[] }).map(|_| ())
    }

    fn state(&self) -> TunnelState {
        self.report().state
    }

    fn report(&self) -> TunnelReport {
        self.ask(&Request { command: Command::Status, conf: None, kill_switch: false, local_around: false, around: &[] })
            .unwrap_or(TunnelReport { state: TunnelState::Down, warning: None })
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
        let options = TunnelOptions { kill_switch: true, local_around: true, around: vec!["10.96.0.0/12".to_owned()] };
        tunnel.up(&Secret::new("[Interface]\n".to_owned()), options).unwrap();
        let request: serde_json::Value = serde_json::from_str(&handle.join().unwrap()).unwrap();
        assert_eq!(
            request,
            serde_json::json!({"command": "up", "conf": "[Interface]\n", "kill_switch": true, "local_around": true, "around": ["10.96.0.0/12"]})
        );
    }

    #[test]
    fn shows_the_services_refusal() {
        let (_directory, tunnel, _handle) = service("{\"ok\":false,\"error\":\"сервер не ответил\"}\n");
        let error = tunnel.up(&Secret::new(String::new()), TunnelOptions::default()).unwrap_err();
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
        assert!(tunnel.up(&Secret::new(String::new()), TunnelOptions::default()).unwrap_err().to_string().contains("не запущена"));
    }
}
