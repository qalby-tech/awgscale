//! The link that brings a device into a network:
//!
//! ```text
//! awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k
//! ```
//!
//! `h` is the network's domain (its panel answers there over HTTPS), `c` is the
//! invitation's secret. The page link the panel hands out works too:
//!
//! ```text
//! https://vpn.example.org/i/Zx3kQ0aB1cD2eF3gH4iJ5k
//! ```
//!
//! The secret The secret is checked by the server; here only the shape
//! of both is, so that nothing but a domain of the internet is ever asked.

use url::Url;

use crate::error::Error;

/// The scheme the apps register for.
pub const SCHEME: &str = "awgscale";
/// What the link asks the app to do.
pub const JOIN_ACTION: &str = "join";
/// The query key of the network's domain.
pub const HOST_KEY: &str = "h";
/// The query key of the invitation's secret.
pub const CODE_KEY: &str = "c";
/// The scheme of the invitation's page.
pub const PAGE_SCHEME: &str = "https";
/// The path of the invitation's page before its secret.
pub const PAGE_PREFIX: &str = "/i/";
/// The length of an invitation's secret, as the panel accepts it (`INVITE_RE` in panel/routes/invites.py).
pub const CODE_LENGTH: usize = 22;
const HOST_MAX_LENGTH: usize = 253;
const LABEL_MAX_LENGTH: usize = 63;
const TOP_LABEL_MIN_LENGTH: usize = 2;
const TOP_LABEL_MAX_LENGTH: usize = 24;

/// Where a device goes to join, and with which secret.
#[derive(Clone, PartialEq, Eq)]
pub struct JoinLink {
    host: String,
    code: String,
}

impl std::fmt::Debug for JoinLink {
    /// The secret is never printed, not even in a log of a crash.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("JoinLink").field("host", &self.host).field("code", &"<hidden>").finish()
    }
}

impl JoinLink {
    /// Reads a link someone pasted or scanned. Spaces around it are forgiven; nothing else is.
    pub fn parse(text: &str) -> Result<Self, Error> {
        let address = Url::parse(text.trim()).map_err(|_| Error::BadLink)?;
        if address.scheme() == PAGE_SCHEME {
            return Self::page(&address);
        }
        if address.scheme() != SCHEME || address.host_str() != Some(JOIN_ACTION) {
            return Err(Error::BadLink);
        }
        let mut host: Option<String> = None;
        let mut code: Option<String> = None;
        for (key, value) in address.query_pairs() {
            let slot = match key.as_ref() {
                HOST_KEY => &mut host,
                CODE_KEY => &mut code,
                _ => continue,
            };
            if slot.is_some() {
                return Err(Error::BadLink); // a key given twice: which one did the sender mean?
            }
            *slot = Some(value.into_owned());
        }
        let host = host.map(|value| value.to_ascii_lowercase()).ok_or(Error::BadLink)?;
        let code = code.ok_or(Error::BadLink)?;
        if !is_domain(&host) || !is_code(&code) {
            return Err(Error::BadLink);
        }
        Ok(Self { host, code })
    }

    /// The invitation's page: https://<domain>/i/<secret>, nothing more (no port, no user, no query).
    fn page(address: &Url) -> Result<Self, Error> {
        let host = address.host_str().map(str::to_ascii_lowercase).ok_or(Error::BadLink)?;
        let code = address.path().strip_prefix(PAGE_PREFIX).map(|rest| rest.trim_end_matches('/')).ok_or(Error::BadLink)?;
        let plain = address.port().is_none() && address.username().is_empty() && address.password().is_none() && address.query().is_none();
        if !plain || !is_domain(&host) || !is_code(code) {
            return Err(Error::BadLink);
        }
        Ok(Self { host, code: code.to_owned() })
    }

    /// The network's domain.
    pub fn host(&self) -> &str {
        &self.host
    }

    /// The invitation's secret.
    pub fn code(&self) -> &str {
        &self.code
    }
}

/// An invitation's secret: 22 characters of URL-safe base64.
pub fn is_code(text: &str) -> bool {
    text.len() == CODE_LENGTH && text.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

/// A domain name of the internet, lower case: labels of letters, digits and inner hyphens,
/// a top label of letters only. No address, no port, no user, no `localhost`.
pub fn is_domain(text: &str) -> bool {
    if text.is_empty() || text.len() > HOST_MAX_LENGTH {
        return false;
    }
    let labels: Vec<&str> = text.split('.').collect();
    let Some((top, rest)) = labels.split_last() else {
        return false;
    };
    if rest.is_empty() {
        return false;
    }
    let top_fits = (TOP_LABEL_MIN_LENGTH..=TOP_LABEL_MAX_LENGTH).contains(&top.len()) && top.bytes().all(|byte| byte.is_ascii_lowercase());
    top_fits && rest.iter().all(|label| is_label(label))
}

fn is_label(label: &str) -> bool {
    let bytes = label.as_bytes();
    let (Some(first), Some(last)) = (bytes.first(), bytes.last()) else {
        return false;
    };
    label.len() <= LABEL_MAX_LENGTH
        && first.is_ascii_alphanumeric()
        && last.is_ascii_alphanumeric()
        && bytes.iter().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    const CODE: &str = "Zx3kQ0aB1cD2eF3gH4iJ5k";

    #[test]
    fn reads_a_good_link() {
        let link = JoinLink::parse(&format!("  awgscale://join?h=VPN.Example.org&c={CODE}\n")).unwrap();
        assert_eq!(link.host(), "vpn.example.org");
        assert_eq!(link.code(), CODE);
    }

    #[test]
    fn refuses_what_is_not_a_join_link() {
        let refused = [
            format!("https://join?h=vpn.example.org&c={CODE}"),
            format!("awgscale://open?h=vpn.example.org&c={CODE}"),
            "awgscale://join?h=vpn.example.org".to_owned(),
            format!("awgscale://join?c={CODE}"),
            format!("awgscale://join?h=vpn.example.org&c={CODE}x"),
            "awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5!".to_owned(),
            format!("awgscale://join?h=vpn.example.org&h=evil.example.org&c={CODE}"),
            format!("awgscale://join?h=127.0.0.1&c={CODE}"),
            format!("awgscale://join?h=localhost&c={CODE}"),
            format!("awgscale://join?h=vpn.example.org:8443&c={CODE}"),
            format!("awgscale://join?h=user@vpn.example.org&c={CODE}"),
            format!("awgscale://join?h=vpn.example.org/path&c={CODE}"),
            format!("awgscale://join?h=-vpn.example.org&c={CODE}"),
            format!("awgscale://join?h=172.16.17.1&c={CODE}"),
            "not a link".to_owned(),
        ];
        for text in refused {
            assert!(JoinLink::parse(&text).is_err(), "{text}");
        }
    }

    #[test]
    fn reads_the_panels_page_link() {
        let link = JoinLink::parse(&format!("https://VPN.example.org/i/{CODE}")).unwrap();
        assert_eq!((link.host(), link.code()), ("vpn.example.org", CODE));
        for refused in [
            format!("http://vpn.example.org/i/{CODE}"),
            format!("https://vpn.example.org:8443/i/{CODE}"),
            format!("https://user@vpn.example.org/i/{CODE}"),
            format!("https://vpn.example.org/i/{CODE}/info"),
            format!("https://vpn.example.org/x/{CODE}"),
            format!("https://vpn.example.org/i/{CODE}?a=b"),
            format!("https://127.0.0.1/i/{CODE}"),
        ] {
            assert!(JoinLink::parse(&refused).is_err(), "{refused}");
        }
    }

    #[test]
    fn never_prints_the_secret() {
        let link = JoinLink::parse(&format!("awgscale://join?h=vpn.example.org&c={CODE}")).unwrap();
        assert!(!format!("{link:?}").contains(CODE));
    }
}
