"""The site or service a looked-up name belongs to.

AdGuard's own "top queried domains" is not it: it counts every client and is
full of technical names. Here technical names are folded into the service a
person would name, and the phones' own background chatter is left out.
"""
from __future__ import annotations

SERVICES: dict[str, str] = {
    "YouTube": "youtube.com youtu.be googlevideo.com ytimg.com ggpht.com youtube-nocookie.com youtubei.googleapis.com youtube.googleapis.com",
    "Google Поиск": "www.google.com www.google.ru google.ru",
    "Gmail и Google Диск": "mail.google.com gmail.com docs.google.com drive.google.com",
    "Google Meet": "meet.google.com",
    "Gemini": "gemini.google.com aistudio.google.com notebooklm.google.com",
    "ChatGPT": "openai.com chatgpt.com oaistatic.com oaiusercontent.com",
    "Claude": "claude.ai claude.com anthropic.com",
    "Telegram": "telegram.org t.me telegram.me telegra.ph cdn-telegram.org",
    "WhatsApp": "whatsapp.com whatsapp.net wa.me",
    "Instagram": "instagram.com cdninstagram.com",
    "Facebook": "facebook.com fbcdn.net fb.com messenger.com",
    "X (Twitter)": "x.com twitter.com twimg.com t.co",
    "Discord": "discord.com discord.gg discordapp.com discordapp.net discord.media",
    "TikTok": "tiktok.com tiktokcdn.com tiktokv.com byteoversea.com",
    "ВКонтакте": "vk.com vk.ru vk.me userapi.com vk-cdn.net vkuser.net vkuservideo.net vkvideo.ru",
    "Одноклассники": "ok.ru okcdn.ru mycdn.me",
    "Mail.ru": "mail.ru imgsmail.ru",
    "Яндекс": "yandex.ru yandex.net yandex.com ya.ru yastatic.net",
    "Дзен": "dzen.ru",
    "Кинопоиск": "kinopoisk.ru",
    "Rutube": "rutube.ru",
    "Ozon": "ozon.ru ozone.ru ozonusercontent.com",
    "Wildberries": "wildberries.ru wb.ru wbbasket.ru wbstatic.net",
    "Авито": "avito.ru avito.st",
    "Т-Банк": "tbank.ru t-bank-app.ru tinkoff.ru tcsbank.ru",
    "Сбер": "sberbank.ru sber.ru",
    "Госуслуги": "gosuslugi.ru",
    "2ГИС": "2gis.ru 2gis.com",
    "RuStore": "rustore.ru",
    "Netflix": "netflix.com nflxvideo.net nflximg.net nflxso.net nflxext.com",
    "Spotify": "spotify.com scdn.co spotifycdn.com",
    "Twitch": "twitch.tv ttvnw.net jtvnw.net",
    "WeChat": "wechat.com wechatapp.com weixin.qq.com",
    "GitHub": "github.com githubusercontent.com githubassets.com",
    "Steam": "steampowered.com steamcommunity.com steamstatic.com steamcontent.com",
    "LinkedIn": "linkedin.com licdn.com",
    "Reddit": "reddit.com redd.it redditstatic.com redditmedia.com",
    "Википедия": "wikipedia.org wikimedia.org",
    "Zoom": "zoom.us",
    "Notion": "notion.so notion.com",
    "Binance": "binance.com bnbstatic.com",
}
# Not something a person opens: the operating system, app plumbing, CDNs,
# certificate checks, clocks, connectivity probes, our own self-test.
BACKGROUND: list[str] = (
    "google.com googleapis.com gstatic.com googleusercontent.com gvt1.com gvt2.com gvt3.com 1e100.net android.com "
    "googletagmanager.com google-analytics.com doubleclick.net googlesyndication.com googleadservices.com "
    "app-measurement.com crashlytics.com firebaseio.com "
    "apple.com icloud.com icloud-content.com mzstatic.com cdn-apple.com aaplimg.com apple-dns.net me.com "
    "samsung.com samsungcloud.com samsungapps.com samsungdm.com samsungqbe.com samsungosp.com samsungcloudsolution.com "
    "xiaomi.com xiaomi.net miui.com mi.com huawei.com hicloud.com dbankcloud.com dbankcloud.ru "
    "microsoft.com windows.com windowsupdate.com live.com msftconnecttest.com msftncsi.com office.com office365.com msn.com "
    "ntp.org cloudflare.com cloudflare.net cloudflare-dns.com cloudfront.net amazonaws.com akadns.net akamai.net "
    "akamaiedge.net akamaihd.net akamaized.net edgekey.net edgesuite.net fastly.net fastlylb.net azureedge.net azure.com "
    "trafficmanager.net kaspersky-labs.com kaspersky.com drweb.com digicert.com lencr.org letsencrypt.org pki.goog "
    "sectigo.com globalsign.com usertrust.com example.org example.com example.net ifconfig.me arpa local lan"
).split()
TWO_LEVEL: set[str] = {"co.uk", "org.uk", "com.ru", "org.ru", "net.ru", "msk.ru", "spb.ru", "com.tr", "com.au", "co.jp", "com.br",
             "co.kr", "com.ua", "co.il", "com.cn", "com.kz", "co.id", "com.sg"}


def _index() -> dict[str, str | None]:
    """Every known suffix → the service it belongs to; None for background noise."""
    index: dict[str, str | None] = {domain: None for domain in BACKGROUND}
    for service, domains in SERVICES.items():
        for domain in domains.split():
            index[domain] = service
    return index


SITE_INDEX = _index()
OWN_DOMAIN_LABELS = 2                   # example.com
OWN_DOMAIN_LABELS_TWO_LEVEL = 3         # example.co.uk


def site_of(host: str) -> str | None:
    """The service or site a lookup belongs to; None for background noise.
    The longest known suffix decides: www.google.com is the search site even
    though google.com as a whole is counted as background."""
    labels = host.rstrip(".").lower().split(".")
    for start in range(len(labels)):
        tail = ".".join(labels[start:])
        if tail in SITE_INDEX:
            return SITE_INDEX[tail]
    if len(labels) < OWN_DOMAIN_LABELS:
        return None
    two_level = len(labels) > OWN_DOMAIN_LABELS and ".".join(labels[-OWN_DOMAIN_LABELS:]) in TWO_LEVEL
    return ".".join(labels[-OWN_DOMAIN_LABELS_TWO_LEVEL:] if two_level else labels[-OWN_DOMAIN_LABELS:])
