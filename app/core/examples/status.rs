//! Asks the installed service what the tunnel does, the way the app does (ServiceTunnel): for the
//! installer's check on Windows, run as an ordinary person.

use awgscale_client::service::ServiceTunnel;
use awgscale_client::tunnel::Tunnel;

fn main() {
    let report = ServiceTunnel::usual().report();
    println!("{}", serde_json::to_string(&report).unwrap_or_default());
}
